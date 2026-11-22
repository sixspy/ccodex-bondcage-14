"""拆笼分拨：整笔事务切分，父笼关闭（已拆笼），同事务落两只独立子笼。

口径（见 docs/子笼扩额纪要.md，2026-11-22 锁死）：
- 仅实现「整笔切分」；「影子子笼」模式不在本仓口径内，请求即拒。
- 同事务：父笼 available 清零并置「已拆笼」，插入两只子笼（parent_id 指回父笼），
  登记 split_jobs、写审计；任一失败整笔回滚，保证账户冻结额与活笼可用恒等。
- 拆笼不动 bond_accounts：冻结总额在父子之间平移，账户侧零改。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from auth import AuthHeader, require_user, require_writer
from db import connect, now_iso, write_audit
from locks import release, require_lock
from models import SplitIn

router = APIRouter(tags=["split"])

ALLOWED_MODE = "整笔切分"
REJECTED_MODE = "影子子笼"
SPLIT_DONE = "已拆笼"


@router.get("/api/split/jobs")
def list_jobs(authorization: AuthHeader = None):
    require_user(authorization)
    with connect() as conn:
        rows = conn.execute(
            """SELECT j.*, c.cage_code FROM split_jobs j
               JOIN cages c ON c.id=j.cage_id
               ORDER BY j.id DESC"""
        ).fetchall()
        return {"items": [dict(r) for r in rows]}


def _insert_child(conn, *, code: str, parent, amount: float, now: str) -> int:
    return conn.execute(
        """INSERT INTO cages(cage_code,party_key,contract_no,amount,available,
           generation,status,beneficiary,created_at,frozen_at,parent_id,root_cage_code)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            code,
            parent["party_key"],
            parent["contract_no"],
            amount,
            amount,
            1,
            "已冻结",
            parent["beneficiary"],
            now,
            now,
            parent["id"],
            parent["cage_code"],
        ),
    ).lastrowid


@router.post("/api/split")
def split_cage(body: SplitIn, authorization: AuthHeader = None):
    user = require_writer(authorization)
    if body.mode == REJECTED_MODE:
        # 影子子笼模式与本仓整笔事务口径互斥，拒写并留审计
        with connect() as conn:
            write_audit(
                conn,
                user,
                "split_yaw",
                str(body.cage_id),
                {"reason": "影子子笼模式被纪要禁用", "mode": body.mode},
            )
            conn.commit()
        raise HTTPException(409, "影子子笼模式已禁用：拆笼只走整笔切分（纪要锁死）")
    if body.mode != ALLOWED_MODE:
        raise HTTPException(400, "mode 须为整笔切分")

    left = float(body.left_amount)
    right = float(body.right_amount)
    lock_key = f"cage:{body.cage_id}"
    with connect() as conn:
        require_lock(conn, lock_key, user)
        try:
            parent = conn.execute(
                "SELECT * FROM cages WHERE id=?", (body.cage_id,)
            ).fetchone()
            if not parent:
                raise HTTPException(404, "笼不存在")
            if parent["status"] == SPLIT_DONE:
                raise HTTPException(409, "父笼已拆笼关闭，禁止再拆")
            if left <= 0 or right <= 0:
                raise HTTPException(400, "左右金额均须大于 0")
            if abs(left + right - float(parent["available"])) > 1e-6:
                raise HTTPException(400, "左右金额之和须等于笼可用额")
            # 编码防重
            left_code = f"{parent['cage_code']}-L"
            right_code = f"{parent['cage_code']}-R"
            for code in (left_code, right_code):
                if conn.execute(
                    "SELECT id FROM cages WHERE cage_code=?", (code,)
                ).fetchone():
                    raise HTTPException(409, f"子笼编码已存在：{code}")

            now = now_iso()
            job = conn.execute(
                """INSERT INTO split_jobs(cage_id,left_amount,right_amount,mode,status,detail,created_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (
                    body.cage_id,
                    left,
                    right,
                    ALLOWED_MODE,
                    SPLIT_DONE,
                    "整笔切分：父笼关闭，子笼独立",
                    now,
                ),
            )
            left_id = _insert_child(
                conn, code=left_code, parent=parent, amount=left, now=now
            )
            right_id = _insert_child(
                conn, code=right_code, parent=parent, amount=right, now=now
            )
            # 父笼关闭：金额保留历史口径，可用清零
            conn.execute(
                "UPDATE cages SET available=0.0, status=? WHERE id=?",
                (SPLIT_DONE, body.cage_id),
            )
            write_audit(
                conn,
                user,
                "cage_split",
                parent["cage_code"],
                {
                    "split_job_id": job.lastrowid,
                    "parent_id": parent["id"],
                    "left_child_id": left_id,
                    "right_child_id": right_id,
                    "left": left,
                    "right": right,
                    "mode": ALLOWED_MODE,
                },
            )
            conn.commit()
            return {
                "split_job_id": job.lastrowid,
                "status": SPLIT_DONE,
                "parent_id": parent["id"],
                "left_child_id": left_id,
                "right_child_id": right_id,
                "account_unchanged": True,
            }
        except Exception:
            conn.rollback()
            raise
        finally:
            release(conn, lock_key, user)
            conn.commit()
