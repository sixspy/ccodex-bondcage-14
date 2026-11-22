# 保证金笼

> 遵守 `ops/底座生成前必读_效率与技术栈.md`（≥2000 行像产品）。  
> FastAPI + Vue 3 + SQLite；`docker compose up -d --build` 一键起。  
> **禁止同构换皮**：主链路不是「占位→确认入册」也不是「入轨→裁定赔付」也不是「业务窗裁切物化」。

## 骨架一句话

保证金先入笼冻结，履约核验通过才解笼拨付，违约进罚没册，笼代与拨付罚没对账。

## 基本信息

| 项 | 值 |
|----|-----|
| 产品名 | 保证金笼 |
| 目录 | `11-bondcage` |
| 接口 | http://127.0.0.1:11324 |
| 库 | `data/app.db` |
| 账号 | admin / clerk / viewer ，密码均为 123456（viewer 只读） |

## 厚度

| 项 | 内容 |
|----|------|
| 业务 LOC | **≥2900**（py+前端，不含 vue.global / Docker / README；自检约 2984） |
| 后端模块 | db / auth / locks / bonds_svc / cages_svc / attest_svc / release_svc / forfeit_svc / reconcile_svc / audit_svc / topup_svc / split_svc |
| 前端视图 | 保证金户 / 入笼冻结 / 履约核验 / 解笼拨付 / 罚没册 / 对账互证 / 审计争用 / 增补拆笼（8 页） |
| 已落地 | topup（子笼独立扩额，甲路径）、split（整笔切分拆笼）——口径见 `docs/子笼扩额纪要.md`（2026-11-22 当日锁死） |

## 子笼扩额口径（锁死，勿改）

- 唯一正确性口径＝**甲路径：子笼独立扩代整笔**。对目标子笼编号同事务从账户可用余额划入、
  抬额升代写审计；失败则账户与子笼整笔同滚。
- 乙路径（经已关闭父笼桥接影子单再晋升）**禁用**；旧「同笼扩代 / 新笼桥接」等模式请求一律记
  审计 `child_topup_yaw`（子笼扩额偏航）并 409 拒写。
- 子笼扩额只按子笼 `id` 落账，与父笼切断；已关闭父笼逐字段零改（提交后有隔离断言兜底）。
- 两人同扩同一子笼抢 `cage:{id}` 锁，后到只写 `contention_log` 争用痕并 409，不写业务。
- 五页（增补/拆笼/入笼/账户/对账）同源于 `data/app.db`：账户冻结＝活笼可用合计；
  笼侧 `available + 同代拨付 + 同代罚没（已拆笼父笼另加拆笼平移额）= amount`。
- 库文件保持 SQLite 不换（仅加列 `cages.parent_id/root_cage_code` 与新表 `child_topup_minutes`）；页面登录保留。

## 主链路

开户入金 → 按合约入笼冻结（笼代）→ 履约核验通过 → 解笼拨付到收款方 / 违约罚没 → 笼代余额与拨付+罚没对账

## 互斥切口与择定

- 解笼失败整笔回滚 vs 影子拨付后晋升
- 罚没立即入册 vs 影子罚没待复核晋升
- 增补同笼扩代 vs 新笼桥接双写 → **已择定并锁死：子笼独立扩代整笔（甲路径），桥接禁用**
- 拆笼整笔事务切分 vs 影子子笼后晋升 → **已择定：整笔切分，影子子笼请求即拒（split_yaw）**

## 启动

```powershell
cd projects/11-bondcage
docker compose up -d --build
```

浏览器：http://127.0.0.1:11324/

本机开发：

```powershell
python -m pip install -r requirements.txt
python -m uvicorn app:app --app-dir backend --host 127.0.0.1 --port 10608
python -m pytest -q
```
