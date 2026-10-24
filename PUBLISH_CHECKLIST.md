# 上架自检清单（PUBLISH CHECKLIST）

## 一、发布物料（首次上传需备齐 6 类）

- [ ] **① PNG 图标**（包外单独上传）—— `icon.png`，512×512，**不要放进 zip**
- [ ] **② zip 包**（**无任何图片/二进制**，纯文本）—— 运行 `python build_dist.py` 生成
- [ ] **③ 显示名称** —— `AI 数据核对`
- [ ] **④ slug** —— `ai-data-lineage-guard`
- [ ] **⑤ 描述** —— 见 `skillhub_meta.json` 的 `short_description` / `long_description`
- [ ] **⑥ 分类/标签/版本** —— 分类 `开发工具`；版本 `0.3.1`；标签见 `skillhub_meta.json`

> ⚠️ **压缩包里不能含任何图片**（png/jpg/svg/ico 等二进制）。上传接口会拦截并报
> 「不支持的文件类型」。图标必须在表单的「图标 → 自定义」处**单独上传**。

## 二、打包自检（`build_dist.py` 已自动执行）

- [x] zip 内无 `.png/.jpg/.xlsx/.parquet/.db/.pyc/.log` 等二进制
- [x] zip 内无点文件（`.gitignore` / `.DS_Store`）
- [x] `skillhub_meta.json` 随包自带（纯文本，供填表复制）
- [x] `trial/` 试跑证据已排除（含 xlsx，体积大且非发布必需）
- [x] `icon.png` 被排除在包外

**人工复核**：`python -c "import zipfile;print('\n'.join(zipfile.ZipFile('ai-data-lineage-guard.zip').namelist()))"`
逐条确认无图片、无二进制。

## 三、metadata 与 frontmatter 一致性

- [x] `SKILL.md` frontmatter 含 `name / displayName / version / author / category / tags / summary / description / license / pricing / platforms / permission / dependency / trigger / icon`
- [x] frontmatter 的 `version` = `skillhub_meta.json` 的 `version` = `0.3.1`
- [x] frontmatter 的 `displayName` = meta 的 `display_name`
- [x] 两处 `category` 均为 `开发工具`
- [x] 两处 `tags` 一致（8 个）

## 四、功能可用性（发布前必跑）

- [ ] `python emit.py --source samples/source_sales.csv --claims samples/ai_output_clean.json --out /tmp/t.md` → **exit 0**，写出交付文件
- [ ] `python emit.py --source samples/source_sales.csv --claims samples/ai_output_manifest.json --out /tmp/f.md` → **exit 1**，不写交付文件，写 `.blocked.md`
- [ ] `python audit_semantic.py prepare --source trial/semantic_source.json --claims trial/semantic_claims.json --out /tmp/p.json` → **exit 0**
- [ ] 依赖声明一致：csv/json 零依赖；xlsx 需 `openpyxl`（缺依赖时显式报错，不静默跳过）

## 五、安全与诚实性（SkillHub 审核重点）

- [x] **不含任何密钥 / token / 私有路径**（脚本内无硬编码本机绝对路径作为必需项）
- [x] **不修改用户的原始数据**（只读源文件，只写校验报告）
- [x] **诚实边界已写明**：只验证过 1 个真实任务 + 若干样本，发布不等于验证充分
- [x] **适用范围已写明**：结构化数值产出核对；不适用于自由文本质量校验 / 代码验证
- [x] **协议门槛已前置说明**：`exit 2` 是「未按协议写」，不是「通过」也不是「工具坏了」
- [x] **强制力边界已写明**：协议层强制；真·硬闸门需框架级 pre-delivery hook 调 `emit.py`

## 六、提交

- [ ] 打开 https://skillhub.cn → 微信扫码登录 → 「发布技能 / 上传技能」
- [ ] 上传 `ai-data-lineage-guard.zip`
- [ ] 按上面 ③–⑥ 填表，图标单独传 `icon.png`
- [ ] 权限说明：只读你的数据文件并写校验报告，不修改原始数据
- [ ] 定价：免费
- [ ] 提交审核

## 七、后续更新只需备 2 类

- [ ] 新 zip 包（重跑 `python build_dist.py`）
- [ ] 更新描述（描述或版本有变时同步 `skillhub_meta.json` 与 frontmatter）
- [ ] 除非换图标，**无需再传 PNG**