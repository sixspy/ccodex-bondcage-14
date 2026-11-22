"""增补 / 拆笼落地后的真实路径测试（替代原空桩测试）。

口径见 docs/子笼扩额纪要.md（2026-11-22 锁死）：
- 拆笼只走整笔切分，影子子笼模式拒写；
- 子笼扩额只走甲路径（独立扩代整笔），乙路径/旧空桩模式记偏航并拒写；
- 五页同源：账户冻结 == 活笼可用合计，笼侧 available+拨付+罚没 == amount。
"""
import pytest
from fastapi.testclient import TestClient

from app import app
from db import connect, init_db


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


def _make_cage(c: TestClient, h: dict, party: str, code: str, amount: float):
    return c.post(
        "/api/cages/freeze",
        headers=h,
        json={
            "party_key": party,
            "contract_no": f"HT-{code}",
            "amount": amount,
            "beneficiary": "业主方",
            "cage_code": code,
        },
    ).json()


def test_split_real_transaction_creates_two_independent_children():
    c = TestClient(app)
    h = _auth(c)
    cage = _make_cage(c, h, "P-WEST", "CG-WEST-S1", 50000.0)
    r = c.post(
        "/api/split",
        headers=h,
        json={
            "cage_id": cage["id"],
            "left_amount": 30000.0,
            "right_amount": 20000.0,
            "mode": "整笔切分",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    # 账户零改：拆笼只在笼内平移冻结
    with connect() as conn:
        acct = conn.execute(
            "SELECT frozen FROM bond_accounts WHERE party_key='P-WEST'"
        ).fetchone()["frozen"]
        parent = conn.execute(
            "SELECT * FROM cages WHERE id=?", (cage["id"],)
        ).fetchone()
        left = conn.execute(
            "SELECT * FROM cages WHERE id=?", (body["left_child_id"],)
        ).fetchone()
        right = conn.execute(
            "SELECT * FROM cages WHERE id=?", (body["right_child_id"],)
        ).fetchone()
    assert acct == 50000.0
    assert parent["status"] == "已拆笼" and parent["available"] == 0.0
    assert left["available"] == 30000.0 and left["parent_id"] == cage["id"]
    assert right["available"] == 20000.0 and right["parent_id"] == cage["id"]
    # 活笼可用合计仍等于账户冻结
    proof = c.get("/api/proof/P-WEST", headers=h).json()
    assert proof["frozen_matches_live"] is True


def test_split_shadow_mode_rejected_as_yaw():
    c = TestClient(app)
    h = _auth(c)
    cage = _make_cage(c, h, "P-WEST", "CG-WEST-S2", 20000.0)
    r = c.post(
        "/api/split",
        headers=h,
        json={
            "cage_id": cage["id"],
            "left_amount": 10000.0,
            "right_amount": 10000.0,
            "mode": "影子子笼",
        },
    )
    assert r.status_code == 409
    with connect() as conn:
        # 拒写且留偏航审计
        yaw = conn.execute(
            "SELECT * FROM audit_trail WHERE action='split_yaw'"
        ).fetchall()
        assert len(yaw) == 1
        # 没有新增笼
        children = conn.execute(
            "SELECT COUNT(*) c FROM cages WHERE parent_id=?", (cage["id"],)
        ).fetchone()["c"]
        assert children == 0


def test_split_closed_parent_rejected():
    c = TestClient(app)
    h = _auth(c)
    cage = _make_cage(c, h, "P-WEST", "CG-WEST-S3", 20000.0)
    c.post(
        "/api/split",
        headers=h,
        json={
            "cage_id": cage["id"],
            "left_amount": 10000.0,
            "right_amount": 10000.0,
            "mode": "整笔切分",
        },
    )
    again = c.post(
        "/api/split",
        headers=h,
        json={
            "cage_id": cage["id"],
            "left_amount": 1.0,
            "right_amount": 1.0,
            "mode": "整笔切分",
        },
    )
    assert again.status_code == 409


def test_topup_bridge_mode_recorded_as_yaw_and_rejected():
    c = TestClient(app)
    h = _auth(c)
    cage = _make_cage(c, h, "P-WEST", "CG-WEST-T1", 30000.0)
    split = c.post(
        "/api/split",
        headers=h,
        json={
            "cage_id": cage["id"],
            "left_amount": 18000.0,
            "right_amount": 12000.0,
            "mode": "整笔切分",
        },
    ).json()
    child_id = split["left_child_id"]
    before = c.get(f"/api/cages/{child_id}", headers=h).json()["cage"]

    for bad_mode in ("新笼桥接", "同笼扩代"):
        r = c.post(
            "/api/topup/child",
            headers=h,
            json={"cage_id": child_id, "amount": 5000.0, "mode": bad_mode},
        )
        assert r.status_code == 409
    after = c.get(f"/api/cages/{child_id}", headers=h).json()["cage"]
    # 偏航拒写：子笼零改
    assert after["amount"] == before["amount"]
    assert after["generation"] == before["generation"]
    with connect() as conn:
        yaws = conn.execute(
            "SELECT * FROM audit_trail WHERE action='child_topup_yaw'"
        ).fetchall()
        assert len(yaws) == 2
        jobs = conn.execute(
            "SELECT COUNT(*) c FROM topup_jobs WHERE cage_id=?", (child_id,)
        ).fetchone()["c"]
        assert jobs == 0


def test_topup_on_root_cage_is_yaw():
    c = TestClient(app)
    h = _auth(c)
    cage = _make_cage(c, h, "P-WEST", "CG-WEST-T2", 30000.0)
    r = c.post(
        "/api/topup/child",
        headers=h,
        json={"cage_id": cage["id"], "amount": 5000.0},
    )
    assert r.status_code == 409
    with connect() as conn:
        yaw = conn.execute(
            "SELECT action FROM audit_trail WHERE action='child_topup_yaw'"
        ).fetchall()
        assert len(yaw) == 1


def test_topup_insufficient_free_balance_rolls_back():
    c = TestClient(app)
    h = _auth(c)
    # P-WEST 余额 150000、无冻结，入笼 140000 后可用仅 10000
    cage = _make_cage(c, h, "P-WEST", "CG-WEST-T3", 140000.0)
    split = c.post(
        "/api/split",
        headers=h,
        json={
            "cage_id": cage["id"],
            "left_amount": 90000.0,
            "right_amount": 50000.0,
            "mode": "整笔切分",
        },
    ).json()
    child_id = split["left_child_id"]
    r = c.post(
        "/api/topup/child",
        headers=h,
        json={"cage_id": child_id, "amount": 50000.0},
    )
    assert r.status_code == 409
    with connect() as conn:
        acct = conn.execute(
            "SELECT balance,frozen FROM bond_accounts WHERE party_key='P-WEST'"
        ).fetchone()
        child = conn.execute(
            "SELECT amount,available,generation FROM cages WHERE id=?", (child_id,)
        ).fetchone()
    # 整笔同滚：冻结与子笼都不变
    assert acct["frozen"] == 140000.0
    assert child["amount"] == 90000.0 and child["generation"] == 1


def test_minutes_locked_and_served():
    c = TestClient(app)
    h = _auth(c)
    m = c.get("/api/topup/minutes", headers=h).json()
    assert m["locked_date"] == "2026-11-22"
    assert m["chosen_path"].startswith("甲路径")
    assert "乙路径" in m["rejected_path"]
    with connect() as conn:
        # 纪要表单行且无更新接口
        assert conn.execute("SELECT COUNT(*) c FROM child_topup_minutes").fetchone()["c"] == 1


def test_viewer_cannot_topup():
    c = TestClient(app)
    h = _auth(c, "viewer")
    r = c.post(
        "/api/topup/child",
        headers=h,
        json={"cage_id": 1, "amount": 1000.0},
    )
    assert r.status_code == 403


def test_five_pages_same_source_after_split_and_topup():
    """拆笼 + 子笼扩额后：逐笼平衡、当事人冻结恒等、父子谱系同源。"""
    c = TestClient(app)
    h = _auth(c)
    cage = _make_cage(c, h, "P-WEST", "CG-WEST-ALL", 60000.0)
    split = c.post(
        "/api/split",
        headers=h,
        json={
            "cage_id": cage["id"],
            "left_amount": 40000.0,
            "right_amount": 20000.0,
            "mode": "整笔切分",
        },
    ).json()
    c.post(
        "/api/topup/child",
        headers=h,
        json={"cage_id": split["left_child_id"], "amount": 10000.0},
    )

    recon = c.get("/api/reconcile", headers=h).json()
    west = [i for i in recon["items"] if i["party_key"] == "P-WEST"]
    # 三只笼（父 + 两子）全部平衡
    assert all(i["balanced"] for i in west), west
    parent_recon = next(i for i in west if i["cage_id"] == cage["id"])
    # 父笼口径含平移到子笼的金额
    assert parent_recon["split_to_children"] == 60000.0
    assert parent_recon["status"] == "已拆笼"

    proof = c.get("/api/proof/P-WEST", headers=h).json()
    # 账户冻结 = 活笼（两子笼，含扩额）可用合计：60000 + 10000
    assert proof["frozen_matches_live"] is True
    assert proof["account"]["frozen"] == 70000.0
    assert proof["live_cage_available"] == 70000.0

    # 账户页、入笼页、对账页读到的子笼谱系一致（同一母仓来源）
    bond = c.get("/api/bonds/P-WEST", headers=h).json()
    bond_child = next(x for x in bond["cages"] if x["id"] == split["left_child_id"])
    recon_child = next(
        i for i in west if i["cage_id"] == split["left_child_id"]
    )
    assert bond_child["parent_id"] == cage["id"]
    assert recon_child["parent_id"] == cage["id"]
    assert bond_child["root_cage_code"] == recon_child["root_cage_code"] == cage["cage_code"]
    # 金额同源
    assert bond_child["amount"] == recon_child["amount"] == 50000.0
