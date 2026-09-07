# GitHub Pages 部署交接

当前项目采用 GitHub Actions 上传 `site/` 静态制品。工作流位置：
`.github/workflows/deploy-pages.yml`。

## 当前安全边界

- 工作流只允许人工触发，尚未启用按 `main` 分支推送或定时自动部署。
- 部署前校验 `site/data/site-manifest.json` 中的全部文件和 SHA-256。
- 工作流要求 `publication_status=published`。当前真实站点仍为 `preview`，因此不能绕过批次级人工确认直接发布。
- 当前没有配置 GitHub 账号、目标仓库、远程地址或自定义域名，也没有生成 `CNAME`。

## 首次部署前置条件

1. 需求方指定 GitHub owner 和 repository。
2. 明确仓库使用公开还是私有可见性，并确认相应 GitHub Pages 套餐可用。
3. 操作者对仓库拥有管理员或维护者权限。
4. 在仓库 Settings → Pages 中将 Source 设为 GitHub Actions。
5. 完成批次级人工确认，并生成 `publication_status=published` 的站点制品。
6. 推送代码后，人工触发 `Deploy approved site to GitHub Pages` 工作流。

GitHub.com 项目站点的默认地址通常形如：
`https://<owner>.github.io/<repository>/`。在 owner 和 repository 未确定前，不应写入或展示猜测网址。

## 本地制品校验

```powershell
./.venv/Scripts/python.exe -m literature_intelligence.pipeline.site_artifact `
  --site-dir site `
  --require-publication-status preview
```

正式部署工作流会将最后一项改为 `published`。
