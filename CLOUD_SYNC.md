# 持仓云同步（Supabase）

## 一、怎么用（30 秒）

打开「持仓」页 → 点右侧 **同步**：

| 场景 | 操作 |
|---|---|
| **第一次用** | 点「同步」→ 自动生成 12 位同步码并上传当前持仓 → 把码抄下来 |
| **换了电脑/浏览器** | 点「同步」→ 把同步码填进去 → 点「绑定」→ 持仓自动恢复 |
| **日常** | 什么都不用做，改动后 3 秒自动上传 |
| **不想同步了** | 点「解绑」（云端数据保留） |

同步码格式：`ABCD-EFGH-JKLM`（12 位，去掉易混字符 I/O/0/1）。

## 二、数据存在哪、安全吗

| 项 | 说明 |
|---|---|
| 存储 | Supabase Postgres（东京/新加坡区域，免费额度 500MB） |
| 服务端看到什么 | **只有密文**。payload 是客户端 AES-256-GCM 加密后的串 |
| 密钥 | 由同步码经 SHA-256 派生，**只存在你本机**，不上传 |
| 行 id | 同步码的哈希，反推不出原码 |
| 公开的 key | `sb_publishable_...` 是 Supabase 设计上可公开的客户端 key，只能按行 id 读写 |
| 丢了同步码 | 云端数据解不开，等于丢失（本机 localStorage 仍在，不影响） |

结论：即使有人拿到数据库里的全部内容，没有你的同步码也解不开。

## 三、运维

- 查看/清理数据：Supabase → SQL Editor → `select * from public.holdings;` / `delete from public.holdings;`
- 删表重建：`output/_supabase_schema.sql` 是完整建表语句（幂等，可重复执行）
- 项目地址：https://supabase.com/dashboard/project/fnvahsyrzmmxwovbsqpc

## 四、自动化测试

```bash
python live_audit.py            # 对线上站点做真实交互测试（23 项）
```

覆盖：总览数值、持仓增删持久化、错误代码拒绝、一键分配、导出下载、
云同步生成/写库/换设备恢复、策略图与十字线、分年记录、全站无 JS 报错。
