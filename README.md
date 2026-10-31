# minimax-voice-clone

用 MiniMax 开放平台克隆你自己的声音，再把文章一键配成音频。装进 AI 编程助手（Claude Code、Codex 等）当 skill 用：说一句话，它就会带你走完克隆、改稿、合成。

- 只用 Python 标准库，不用 `pip install`；装了 ffmpeg 会自动统一响度。
- **默认只报价，不花钱**；你确认价格后它才会真正提交。
- 只用于克隆你自己的声音；发布时请勾选平台的「AI 生成」声明。

## 安装

**最省事**：把下面这句话发给你的 AI 编程助手：

> 帮我装这个 skill：github.com/jakob52123/minimax-voice-clone

**手动装**（Claude Code）：

```
git clone https://github.com/jakob52123/minimax-voice-clone ~/.claude/skills/minimax-voice-clone
```

Codex 放到 `~/.codex/skills/`，其他助手把 `SKILL.md` 给它读即可。

## 开始前要准备

1. 在 [platform.minimaxi.com](https://platform.minimaxi.com) 用手机号注册，完成实名认证（控制台左上角空间名 → 管理我的空间 → 管理 → 空间认证）。
2. 充值：账户钱包 → 立即充值 → 自定义 ¥20（第一次约花 ¥10.5，单次最少 ¥10）。
3. 建 API Key：API Key → 创建 API Key → 点复制图标，然后在终端运行：
   ```
   mkdir -p ~/.config/minimax && pbpaste > ~/.config/minimax/api_key && chmod 600 ~/.config/minimax/api_key
   ```
4. 录一段 1–2 分钟的录音：只有你一个人，没有背景音乐。

## 用法

装好以后，对 AI 助手说：

> 用 minimax-voice-clone 克隆我的声音，录音是 ~/Desktop/我的录音.m4a，然后把 ~/Desktop/文章.md 配成音频。花钱前先问我。

也可以直接跑脚本：

```
python3 scripts/voice_clone.py quote 稿子.txt                              # 报价，不花钱
python3 scripts/voice_clone.py clone 录音.m4a MyVoice2026 --yes            # 克隆（只做一次）
python3 scripts/voice_clone.py speak 稿子.txt MyVoice2026 --out 成品.wav --yes  # 一键成品
```

稿子里段与段之间空一行；停顿写 `<#0.5#>`。

## 价格（2026-10 官网）

| 项 | 价格 |
|---|---|
| 克隆音色 | ¥9.9 / 个，第一次正式合成时扣，只收一次 |
| 合成 | ¥3.5 / 万计费字符（一个汉字算两个），2 分钟口播约 ¥0.45 |

## 测试

```
cd scripts && python3 -m pytest test_voice_clone.py -q
```

测试全部离线，不会用到真实 Key。

## 许可

MIT
