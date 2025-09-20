# Build locales folder for mo files:
```
pip install Babel
pybabel compile -d locales -D messages
```

## 开发要点 & 常见坑
* 所有展示给用户的文案都要用 _('...') 包裹（包括 Markdown 标题、按钮文字、标签、placeholder、tips 等）。
* 切换语言是在同一进程内完成的：用 install(lang) + gr.update(...) 即时刷新。
* 新增/修改文案后，别忘了更新 .po 并重新编译 .mo。
* 如果你后续有复数形式，改用 ngettext（已随 install() 注册到全局）。
* 需要更多语言时，只要在 locales/<lang>/LC_MESSAGES/ 新增/编译对应的 messages.po/.mo，下次重启或刷新时 available_languages() 会自动识别。
