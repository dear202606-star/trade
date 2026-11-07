# 证券身份隔离：已核验裁定台账（待用户签字）

日期：2026-11-08　范围：`series_quarantine` 中的历史指数成分股　状态：**代码与演练已就绪，数据库写入尚未执行**

## 一、结论先行

`每日全量更新` 报「排名候选证券身份仍待核验：BMC, COV, EP, GAS, HAR, LSI, PARA, PCL, SBNY, SCG, SUN, TIE」，**不是因为没人查过**，而是 `db_ref.require_unquarantined_candidates()` 只要看到候选在隔离表里就一律报「待核验」，无法区分「已核验后决定维持隔离」和「确实没查过」。

本台账把前者固化为**不可篡改的裁定记录**，让门禁把「未知的静默剔除」升级为「有证据、可审计的显式排除」。

> **这不是解锁。** 裁定写入后，`series_quarantine` 原封不动，价格序列依旧不能进入选中视图，`full_history_accepted` 依旧为 `False`。被改变的只有一件事：门禁不再把已核验的历史退市股当成「待办」反复上报。

## 二、可复现的数据事实

以下全部由 `research/security_identity_resolutions.py` 从在线库现算，不依赖人工填写。`after_end` = 成分区间结束后仍存在的月度价格观察数；`jumps` = `sina_repaired` 源中相邻月涨跌超过 10 倍或跌破 1/100 的次数。

| 代码 | 成分区间结束 | 退市后残留观察 | 极端跳变 | 独立外部佐证 |
|---|---|---|---|---|
| BMC | 2013-09-11 | 82 | 1 | 已确认（CIK 835729） |
| CBE | 2012-12-03 | 61 | 21 | 待补 |
| COV | 2015-01-27 | 66 | 2 | 待补 |
| EP | 2012-05-25 | **348** | 0 | 已确认（KMI 8-K） |
| GAS | 2016-07-01 | 48 | 1 | 已确认（AGL→Southern Co Gas, CIK 1004155） |
| HAR | 2017-03-13 | 40 | 1 | 已确认（CIK 800459, Item 3.01） |
| LSI | 2014-05-07 | 222 | 0 | 已确认（CIK 703360） |
| PARA | 2025-08-08 | 32 | 0 | 待补（HF 与新浪币种/量纲不符） |
| PCL | 2016-02-22 | 76 | 2 | 待补 |
| SBNY | 2023-03-15 | 62 | 1 | 待补 |
| SCG | 2019-01-02 | 4 | 1 | 已确认（`research/review_scg_original_equity.py`） |
| SUN | 2012-10-05 | **313** | 0 | 已确认（用户 2026-11-05 批准，SEC 8-K + 10-K） |
| TIE | 2012-12-24 | 78 | 20 | 待补 |

`members.end_date` 与独立事实精确对应：EP 2012-05-25 正是 Kinder Morgan 并购生效日；SUN 2012-10-05 正是 Sunoco, Inc. 普通股转换日；GAS 2016-07-01 是 AGL Resources 更名 Southern Company Gas 之日；BMC/HAR 的 SEC 8-K 与成分退出日相差 1–3 天。

**诊断**：退市后仍持续产生价格、且反复出现 10³–10⁴ 量级跳变（CBE 在 0.0044 与 200 之间来回、TIE 在 3 与 15000 之间来回、PARA 的 HF 值 107090 对新浪 14.37），是数据源把同代码新证券或错误量纲拼接进历史序列，不是真实公司事件。这正是隔离要挡住的东西。

`P` 不纳入台账：它是 2026-09-21 加入的**当前成分股**（哨兵区间 2099-12-31），已有独立的 `live_identity_review`，绝不能被自动排除出实时排名池。`CBE` 在隔离表但不在本次报错名单，一并纳入台账以免遗漏。

## 三、边界（五条硬约束）

1. 不修改、不删除 `series_quarantine` 任何一行。
2. 不修改 `prices_monthly` / `nominal_monthly` / `series_pick` / `data_lock` / `members`；演练前后逐表哈希比对，`financial_data_changed: False`。
3. 不代表完整历史被接受：`proof.full_history_accepted = False` 恒成立，门禁会拒绝任何把它改成 `True` 的裁定。
4. 不做双向合并、不自动解除锁；裁定表 append-only，`UPDATE` / `DELETE` / `TRUNCATE` 由数据库触发器直接拒绝。
5. 每次读取都重算规范 JSON 的 `sha256`；证据哈希不符、`resolution` 不是 `exclude_reviewed`、proof 的 symbol 不匹配，一律视为未裁定并阻断。

## 四、必须先说清：还有第二道门

`variant_b.py` 与 `strategy_history.py` 连续调用两道门禁：

```
require_unquarantined_candidates(candidates)   # 本次改造：12 只可由裁定显式排除
historical_lineage.require_complete_history()  # 仍会抛 IR,JCI
```

第二道门因 `research/install_historical_identity_reviews.py` 中 IR / JCI 的 `full_history_accepted=False` **必然抛异常**，且 `variant_b.py` 2026-11-05 已明确移除 `BASELINE_MODE=observe` 豁免。这是有意把「全年口径 B」设为 fail-closed 的设计，不是待修 bug。

**因此：即使本台账全部写入，`每日全量更新` 的 B 口径步骤仍会失败。** 本方案消除的是重复调研与误报口径，不是让该 workflow 变绿。若目标是变绿，需要另一项明确决策（例如在存在未解决身份时沿用上一份已验证产物并显式告警），本次未做。

## 五、待用户签字

代码、SQL、回归测试与 dry-run 演练已完成，**尚未写入任何数据库**。按 `PROJECT_GOAL`（「对已锁历史发现错误时记录影响并提交用户决策，不静默覆盖或自动解锁」）与 SUN 的既有先例，写入须单独决策。裁定表 append-only（触发器拒绝 `UPDATE`/`DELETE`/`TRUNCATE`），**写入是单向、不可撤销的**，这是必须签字的原因。

### 推荐：云端一键写入（不依赖任何本地凭据，换机后同样可用）

`.github/workflows/identity-resolution-apply.yml` 仅 `workflow_dispatch`，默认 `DRY_RUN` 只演练不写库；写入需同时满足 `confirm=WRITE` + 非空 `decided_by` + `ref=main`。凭据只取 GitHub Secrets 的 `SUPABASE_DB_PASSWORD`，本机无需主库密码。

```bash
# 1. 先跑默认演练（回滚、不写库，同时验证 CI 直连主库是否可用）
gh workflow run identity-resolution-apply.yml --repo dear202606-star/trade \
  --ref main -f confirm=DRY_RUN

# 2. 用户批准后执行写入（唯一一条真正写库的命令）
gh workflow run identity-resolution-apply.yml --repo dear202606-star/trade \
  --ref main -f confirm=WRITE -f decided_by="用户批准/2026-11-08"
```

也可在 GitHub Actions 网页手动 Run workflow 填同样两个参数。工作流会在写入后在线复核 13 条裁定的证据哈希，并让真实 `db_ref.require_unquarantined_candidates()` 放行一次，回执上传为 artifact。

### 备选：本机直连（需先补齐主库密码）

```bash
# 默认 dry-run，只生成 output/*rollback*.json 回执
DB_TARGET=neon py -3.11 research/security_identity_resolutions.py
# 主库写入：需先有 output/_supabase_dbpass.txt（本机当前没有，CI 里才有对应 Secret）
DB_TARGET=supabase py -3.11 research/security_identity_resolutions.py \
  --write --decided-by "<批准人/日期>"
```

`--write` 有两道自我保护：拒绝在 Neon 冷备上执行（避免只写进副本），拒绝未传 `--decided-by` 的调用。写入前会再次校验既有裁定无冲突、以及五张金融数据表的哈希未变；重复执行会得到 `already_online` 而不会覆盖。

## 六、仍待补齐（不影响裁定，但影响证据完备度）

`external_evidence_status = partial` 的 6 只（CBE / COV / PARA / PCL / SBNY / TIE）尚缺可直接引用的独立发行人文件。它们的**隔离依据仍成立**（在线库事实已足够结论性），但若要达到 SUN 那种逐点证据标准，还需逐只补 SEC 8-K / Item 3.01 退市通知，并对区间内的跳变做逐点价格链路复核。按 SCG 的先例，此类复核的结论大概率仍是「不接受完整历史」，不会自动解锁。
