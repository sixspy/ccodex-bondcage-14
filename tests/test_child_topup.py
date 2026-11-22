"""子笼独立扩额 —— 失败测先行（TDD 红）。

事故背景：拆笼得到子笼后，给其中一只子笼加额，增补却按父笼编号落账：
子笼额度不动、已关闭父笼仍被改，而增补接口返回成功（假绿）。

本模块只允许通过 tests 夹具直插母仓造事故场景，不依赖尚未实现的拆笼服务。
两条失败测（错位失败测、双人同扩争用失败测）见红后，才准改生产服务。
"""
from __future__ import annotations

import threading

import pytest
from fastapi.testclient import TestClient

from app import app
from db import connect, init_db, now_iso, write_audit


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    monkeypatch.setattr("db.DB_PATH", tmp_path / "t.db")
    init_db()
    yield


def _auth(c: TestClient, user: str = "admin") -> dict:
    tok = c.post(
        "/api/login", json={"username": user, "password": "123456"}
    ).json()["token"]
    return {"Authorization": "Bearer " + tok}


def build_split_fixture():
    """已拆子笼事故夹具：一只已关闭父笼 + 两只子笼，账户冻结覆盖两只子笼。

    直接在母仓落账，模拟拆笼已完成、增补即将错打到父笼的现场。
    返回 (父笼id, 子笼A id, 子笼B id, party_key)。
    """
    now = now_iso()
    with connect() as conn:
        # 当事人账户：余额 200000，冻结 100000（= 两子笼可用之和）
        conn.execute(
            """INSERT INTO bond_accounts(party_key,party_name,balance,frozen,status,created_at)
               VALUES (?,?,?,?,?,?)""",
            ("P-SPLIT", "拆笼测试公司", 200000.0, 100000.0, "正常", now),
        )
        # 已关闭父笼：拆笼后 available=0、状态已拆笼，金额保留历史口径
        cur = conn.execute(
            """INSERT INTO cages(cage_code,party_key,contract_no,amount,available,
               generation,status,beneficiary,created_at,frozen_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                "CG-P-SPLIT-001",
                "P-SPLIT",
                "HT-SPLIT-P",
                100000.0,
                0.0,
                1,
                "已拆笼",
                "业主方",
                now,
                now,
            ),
        )
        parent_id = cur.lastrowid
        child_a = conn.execute(
            """INSERT INTO cages(cage_code,party_key,contract_no,amount,available,
               generation,status,beneficiary,created_at,frozen_at,parent_id,root_cage_code)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "CG-P-SPLIT-001-A",
                "P-SPLIT",
                "HT-SPLIT-P",
                60000.0,
                60000.0,
                1,
                "已冻结",
                "业主方",
                now,
                now,
                parent_id,
                "CG-P-SPLIT-001",
            ),
        ).lastrowid
        child_b = conn.execute(
            """INSERT INTO cages(cage_code,party_key,contract_no,amount,available,
               generation,status,beneficiary,created_at,frozen_at,parent_id,root_cage_code)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "CG-P-SPLIT-001-B",
                "P-SPLIT",
                "HT-SPLIT-P",
                40000.0,
                40000.0,
                1,
                "已冻结",
                "业主方",
                now,
                now,
                parent_id,
                "CG-P-SPLIT-001",
            ),
        ).lastrowid
        conn.execute(
            """INSERT INTO split_jobs(cage_id,left_amount,right_amount,mode,status,detail,created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (parent_id, 60000.0, 40000.0, "整笔切分", "已拆笼", "夹具造数", now),
        )
        write_audit(
            conn,
            "fixture",
            "split_fixture_build",
            "CG-P-SPLIT-001",
            {"parent_id": parent_id, "child_a": child_a, "child_b": child_b},
        )
        conn.commit()
        return parent_id, child_a, child_b, "P-SPLIT"


def _snapshot(conn, cage_id: int) -> dict:
    row = conn.execute(
        "SELECT amount,available,generation,status FROM cages WHERE id=?",
        (cage_id,),
    ).fetchone()
    return dict(row)


# ---------------------------------------------------------------------------
# 失败测 1：扩额错位 —— 增补必须只升目标子笼，父笼（已关闭）零改
# ---------------------------------------------------------------------------
def test_child_topup_must_not_hit_parent_red():
    c = TestClient(app)
    h = _auth(c)
    parent_id, child_a, child_b, party = build_split_fixture()
    topup_amount = 12000.0

    with connect() as conn:
        parent_before = _snapshot(conn, parent_id)
        child_a_before = _snapshot(conn, child_a)
        child_b_before = _snapshot(conn, child_b)
        acct_before = dict(
            conn.execute(
                "SELECT balance,frozen FROM bond_accounts WHERE party_key=?",
                (party,),
            ).fetchone()
        )

    # 对【子笼 A】发起独立扩额
    r = c.post(
        "/api/topup/child",
        headers=h,
        json={
            "cage_id": child_a,
            "amount": topup_amount,
            "mode": "子笼独立扩代整笔",
        },
    )
    assert r.status_code == 200, r.text

    with connect() as conn:
        parent_after = _snapshot(conn, parent_id)
        child_a_after = _snapshot(conn, child_a)
        child_b_after = _snapshot(conn, child_b)
        acct_after = dict(
            conn.execute(
                "SELECT balance,frozen FROM bond_accounts WHERE party_key=?",
                (party,),
            ).fetchone()
        )

    # 事故根因断言：已关闭父笼逐字段零改
    assert parent_after == parent_before, "父笼（已拆笼/已关闭）不得被子笼扩额改动"
    # 目标子笼：抬额升代
    assert child_a_after["amount"] == child_a_before["amount"] + topup_amount
    assert child_a_after["available"] == child_a_before["available"] + topup_amount
    assert child_a_after["generation"] == child_a_before["generation"] + 1
    # 非目标子笼零改
    assert child_b_after == child_b_before
    # 账户：冻结增加、余额不动（资金仍在系统内）
    assert acct_after["frozen"] == acct_before["frozen"] + topup_amount
    assert acct_after["balance"] == acct_before["balance"]


# ---------------------------------------------------------------------------
# 失败测 2：双人同扩同一子笼 —— 后到只留争用痕，业务零写
#
# 说明：FastAPI TestClient 经 anyio portal 串行化请求，线程内并发不会真正重叠，
# 因此用「先持锁者占住 cage:{子笼id}」确定性构造第二人争用；另以直连 SQLite
# 的多线程原子抢锁测，覆盖锁原语在真实并发下只有一人成功。
# ---------------------------------------------------------------------------
def test_two_clerks_topup_same_child_contention_red():
    c = TestClient(app)
    h_holder = _auth(c, "admin")
    h_later = _auth(c, "clerk")
    parent_id, child_a, _child_b, party = build_split_fixture()
    topup_amount = 8000.0
    lock_key = f"cage:{child_a}"

    # 先到者占住笼锁（模拟一笔正在进行、尚未释放的扩额）
    held = c.post(
        "/api/locks/acquire", headers=h_holder, json={"lock_key": lock_key}
    )
    assert held.status_code == 200

    def attempt(headers: dict) -> int:
        r = c.post(
            "/api/topup/child",
            headers=headers,
            json={
                "cage_id": child_a,
                "amount": topup_amount,
                "mode": "子笼独立扩代整笔",
            },
        )
        return r.status_code

    # 后到者（含持锁者本人之外的第二人）争用：409，不写业务
    assert attempt(h_later) == 409
    # 原持锁人自己重试属幂等持锁，可正常完成；先释放再由其成功扩额
    c.post("/api/locks/release", headers=h_holder, json={"lock_key": lock_key})
    assert attempt(h_holder) == 200

    with connect() as conn:
        child = _snapshot(conn, child_a)
        parent = _snapshot(conn, parent_id)
        acct = dict(
            conn.execute(
                "SELECT balance,frozen FROM bond_accounts WHERE party_key=?",
                (party,),
            ).fetchone()
        )
        # 恰有一笔成功扩额，争用被拒，绝不叠加
        assert child["amount"] == 60000.0 + topup_amount
        assert child["available"] == 60000.0 + topup_amount
        assert child["generation"] == 2
        assert acct["frozen"] == 100000.0 + topup_amount
        # 父笼零改
        assert parent["amount"] == 100000.0
        assert parent["available"] == 0.0
        assert parent["status"] == "已拆笼"
        # 后到只留争用痕：恰一条
        traces = conn.execute(
            "SELECT * FROM contention_log WHERE lock_key=?", (lock_key,)
        ).fetchall()
        assert len(traces) == 1
        assert traces[0]["actor"] == "clerk"
        # 后到者不得产生业务 job
        jobs = conn.execute(
            "SELECT COUNT(*) c FROM topup_jobs WHERE cage_id=?", (child_a,)
        ).fetchone()["c"]
        assert jobs == 1


def test_lock_primitive_serializes_real_concurrent_acquire():
    """直连 SQLite 多线程抢同一把锁：恰一人成功，败者留争用痕。"""
    from locks import try_acquire

    build_split_fixture()
    lock_key = "cage:race-1"
    barrier = threading.Barrier(2)
    outcomes: list[bool] = []

    def worker(holder: str):
        with connect() as conn:
            barrier.wait()
            ok = try_acquire(conn, lock_key, holder)
            outcomes.append(ok)
            conn.commit()

    t1 = threading.Thread(target=worker, args=("admin",))
    t2 = threading.Thread(target=worker, args=("clerk",))
    t1.start()
    t2.start()
    t1.join(timeout=30)
    t2.join(timeout=30)

    assert sorted(outcomes) == [False, True]
    with connect() as conn:
        holders = [r["holder"] for r in conn.execute(
            "SELECT holder FROM cage_locks WHERE lock_key=?", (lock_key,)
        ).fetchall()]
        assert len(holders) == 1
        traces = conn.execute(
            "SELECT * FROM contention_log WHERE lock_key=?", (lock_key,)
        ).fetchall()
        assert len(traces) == 1
