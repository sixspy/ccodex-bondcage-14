"""增补入笼：子笼独立扩额（甲路径，docs/子笼扩额纪要.md 当日锁死）。

唯一正确性口径：对【目标子笼编号】同一事务从账户可用余额划入、抬额升代、写审计；
失败则账户与子笼整笔同滚。禁止经父笼桥接回写（乙路径禁用），偏离记
「子笼扩额偏航」（审计 child_topup_yaw）并拒写。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from auth import AuthHeader, require_user, require_writer
from db import connect, load_child_topup_minute, now_iso, write_audit
from locks import release, try_acquire
from models import ChildTopupIn

router = APIRouter(tags=["topup"])

CHOSEN_MODE = "子笼独立扩代整笔"
# 乙路径及历史空桩模式一律视为偏航
YAW_MODES = {"新笼桥接", "经父笼桥接", "影子单晋升", "同笼扩代"}
CLOSED_STATUSES = {"已拆笼", "已解笼", "已罚没"}
LIVE_STATUSES = {"已冻结", "核验通过", "核验违约", "部分解笼", "部分罚没"}


@router.get("/api/topup/jobs")
def list_jobs(authorization: AuthHeader = None):
    require_user(authorization)
    with connect() as conn:
        rows = conn.execute(
            """SELECT j.*, c.cage_code FROM topup_jobs j
               JOIN cages c ON c.id=j.cage_id
               ORDER BY j.id DESC"""
        ).fetchall()
        return {"items": [dict(r) for r in rows]}


@router.get("/api/topup/minutes")
def get_minutes(authorization: AuthHeader = None):
    """子笼扩额纪要（锁死口径只读面）。"""
    require_user(authorization)
    with connect() as conn:
        return load_child_topup_minute(conn)


def _record_yaw(conn, user: str, target: str, reason: str, payload: dict) -> None:
    write_audit(
        conn,
        user,
        "child_topup_yaw",
        target,
        {"reason": reason, **payload},
    )
    conn.commit()


@router.post("/api/topup/child")
def topup_child(body: ChildTopupIn, authorization: AuthHeader = None):
    user = require_writer(authorization)
    lock_key = f"cage:{body.cage_id}"

    # 纪要口径校验：只准甲路径
    with connect() as conn:
        minute = load_child_topup_minute(conn)
        if body.mode != CHOSEN_MODE:
            _record_yaw(
                conn,
                user,
                str(body.cage_id),
                f"模式偏航：{body.mode} 不在锁死口径内",
                {"mode": body.mode, "minute_no": minute["minute_no"]},
            )
            raise HTTPException(409, "子笼扩额偏航：仅允许子笼独立扩代整笔（纪要锁死）")

    with connect() as conn:
        # 抢笼锁：后到只留争用痕，不写业务
        if not try_acquire(conn, lock_key, user):
            conn.commit()  # 仅落 contention_log 争用痕
            raise HTTPException(409, "争用：后到只记争用，不写业务")
        try:
            child = conn.execute(
                "SELECT * FROM cages WHERE id=?", (body.cage_id,)
            ).fetchone()
            if not child:
                raise HTTPException(404, "子笼不存在")
            if child["parent_id"] is None:
                _record_yaw(
                    conn, user, child["cage_code"], "目标不是拆笼子笼",
                    {"cage_id": body.cage_id},
                )
                raise HTTPException(409, "子笼扩额偏航：该接口仅面向拆笼所得子笼")
            if child["status"] in CLOSED_STATUSES:
                raise HTTPException(409, f"子笼状态 {child['status']} 禁止扩额")
            if child["status"] not in LIVE_STATUSES:
                raise HTTPException(409, f"子笼状态 {child['status']} 禁止扩额")

            # 父笼仅作谱系校验，绝不参与写账
            parent = conn.execute(
                "SELECT * FROM cages WHERE id=?", (child["parent_id"],)
            ).fetchone()
            if not parent:
                raise HTTPException(409, "子笼谱系缺失父笼，扩额拒写")
            parent_before = (
                float(parent["amount"]),
                float(parent["available"]),
                int(parent["generation"]),
                parent["status"],
            )

            acct = conn.execute(
                "SELECT * FROM bond_accounts WHERE party_key=?",
                (child["party_key"],),
            ).fetchone()
            if not acct:
                raise HTTPException(404, "保证金户不存在")
            if acct["status"] != "正常":
                raise HTTPException(409, "账户状态禁止划款扩额")
            amount = float(body.amount)
            free = float(acct["balance"]) - float(acct["frozen"])
            if amount > free + 1e-9:
                raise HTTPException(409, "可用保证金不足，子笼扩额拒写")

            now = now_iso()
            old_gen = int(child["generation"])
            new_gen = old_gen + 1
            # 1) 账户划入冻结（balance 不动，frozen 上升）
            conn.execute(
                "UPDATE bond_accounts SET frozen = frozen + ? WHERE party_key=?",
                (amount, child["party_key"]),
            )
            # 2) 目标子笼抬额升代 —— 仅按子笼 id 定位，与父笼切断
            conn.execute(
                """UPDATE cages
                   SET amount = amount + ?, available = available + ?, generation = ?
                   WHERE id=?""",
                (amount, amount, new_gen, body.cage_id),
            )
            # 3) job 登记
            job = conn.execute(
                """INSERT INTO topup_jobs(cage_id,amount,mode,status,detail,created_at)
                   VALUES (?,?,?,?,?,?)""",
                (
                    body.cage_id,
                    amount,
                    CHOSEN_MODE,
                    "子笼扩额成功",
                    f"g{old_gen}→g{new_gen}；独立扩额，与父笼切断",
                    now,
                ),
            )
            write_audit(
                conn,
                user,
                "child_topup",
                child["cage_code"],
                {
                    "topup_job_id": job.lastrowid,
                    "child_cage_id": body.cage_id,
                    "parent_cage_id": child["parent_id"],
                    "amount": amount,
                    "from_generation": old_gen,
                    "to_generation": new_gen,
                    "party_key": child["party_key"],
                    "minute_no": minute["minute_no"],
                },
            )
            # 4) 提交前隔离断言：父笼逐字段零改（防假绿回归）。
            #    必须在 commit 之前，命中即可整笔回滚，账户与子笼同滚。
            parent_after_row = conn.execute(
                "SELECT amount,available,generation,status FROM cages WHERE id=?",
                (child["parent_id"],),
            ).fetchone()
            parent_after = (
                float(parent_after_row["amount"]),
                float(parent_after_row["available"]),
                int(parent_after_row["generation"]),
                parent_after_row["status"],
            )
            if parent_after != parent_before:
                raise HTTPException(500, "父笼被改，触发整笔回滚（子笼扩额隔离断言）")

            conn.commit()

            row = conn.execute(
                "SELECT * FROM cages WHERE id=?", (body.cage_id,)
            ).fetchone()
            return {
                "topup_job_id": job.lastrowid,
                "status": "子笼扩额成功",
                "cage": dict(row),
                "parent_unchanged": True,
                "minute_no": minute["minute_no"],
            }
        except Exception:
            conn.rollback()  # 失败：账户与子笼同滚
            raise
        finally:
            release(conn, lock_key, user)
            conn.commit()
