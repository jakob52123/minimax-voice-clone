"""MiniMax 声音克隆与分段配音；默认离线报价，加 --yes 才允许付费。"""
import argparse
import io
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import uuid
import wave
from pathlib import Path

HOST = "https://api.minimaxi.com"
MODEL = "speech-2.8-hd"
KEY = ""
BILLING_NOTICE = "如果已经提交，请到控制台核对扣费；停止，不重试。"


# 所有输出先遮住当前 Key 和 sk- 开头的字符串。
def say(message, error=False):
    text = str(message).replace(KEY, "[已隐藏]") if KEY else str(message)
    print(re.sub(r"sk-[^\s\"'<>，。；：]+", "[已隐藏]", text),
          file=sys.stderr if error else sys.stdout)


# 参数错误不回显原始参数，防止误把 Key 当参数时泄露。
class Parser(argparse.ArgumentParser):
    # 统一交给主入口输出安全提示。
    def error(self, message):
        raise ValueError("命令参数不正确，请用 --help 查看用法。")


# 按实际扣费规则计算字符，停顿标签也逐字计入。
def billable(text):
    text = text.strip()
    return len(text) + len(re.findall(r"[一-鿿]", text))


# 读取 UTF-8 稿子，按空行分段，每段去掉首尾空白。
def paragraphs(filename):
    text = Path(filename).read_text(encoding="utf-8-sig")
    parts = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if not parts:
        raise ValueError("稿子不能为空。")
    return parts


# 金额固定保留两位小数。
def price(count):
    return f"计费字符={count}，约 ¥{count * 3.5 / 10000:.2f}"


# 只有确认付费后才读取环境变量或 Key 文件。
def load_key(filename):
    global KEY
    KEY = os.environ.get("MINIMAX_API_KEY", "").strip()
    if not KEY:
        try:
            KEY = Path(filename).expanduser().read_text().strip()
        except OSError:
            raise ValueError("无法读取 Key 文件，请设置 MINIMAX_API_KEY 或 --key-file。") from None
    if not KEY or any(c.isspace() for c in KEY):
        raise ValueError("Key 为空或格式不正确。")


# 每次仅发送一次请求，不输出认证头、URL 或原始网络错误。
def fetch(request):
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        raise ValueError(f"HTTP {exc.code}：服务器拒绝请求，请检查权限、余额或服务状态。") from None
    except (urllib.error.URLError, OSError, TimeoutError):
        raise ValueError("网络连接失败或超时，提交状态可能未知。") from None


# 提交接口并检查业务状态，常见错误翻译为大白话。
def post(path, body, content_type="application/json"):
    if isinstance(body, dict):
        body = json.dumps(body).encode()
    raw = fetch(urllib.request.Request(HOST + path, data=body, headers={
        "Authorization": "Bearer " + KEY, "Content-Type": content_type}))
    data = json.loads(raw)
    code = data.get("base_resp", {}).get("status_code")
    hints = {2061: "这把 Key 是包月套餐的，克隆要用控制台『API Key』页新建的按量付费 Key",
             2038: "账号没实名认证：控制台左上角点空间名 → 管理我的空间 → 管理 → 空间认证"}
    if code != 0:
        raise ValueError(hints.get(code, f"接口失败（{code}）：" +
                         str(data.get("base_resp", {}).get("status_msg", "返回格式不正确"))))
    return data


# 离线检查音色名，避免付费后才发现命名不合法。
def check_voice(voice_id):
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{6,254}[A-Za-z0-9]", voice_id):
        raise ValueError("voice_id 须为 8–256 位，英文字母开头，只含字母数字 - _，末尾不能是 - 或 _。")


# 检查参考录音格式、大小和可选的 ffprobe 时长。
def check_recording(filename):
    ref = Path(filename)
    if not ref.is_file() or ref.suffix.lower() not in (".mp3", ".m4a", ".wav"):
        raise ValueError("录音须为存在的 mp3/m4a/wav 文件。")
    if not 0 < ref.stat().st_size <= 20 * 1024 * 1024:
        raise ValueError("录音须非空且大小 ≤ 20MB。")
    probe = shutil.which("ffprobe")
    if probe:
        result = subprocess.run([probe, "-v", "error", "-show_entries", "format=duration",
                                 "-of", "default=noprint_wrappers=1:nokey=1", str(ref)],
                                capture_output=True, text=True, check=True, timeout=30)
        duration = float(result.stdout.strip())
        if not math.isfinite(duration) or not 10 <= duration <= 300:
            raise ValueError("录音时长须为 10–300 秒。")
        say(f"录音时长检查通过：{duration:.2f} 秒。")
    else:
        say("未安装 ffprobe，跳过时长检查；请自行确认录音为 10–300 秒。")
    say("录音格式、大小检查通过。")
    return ref


# 克隆先离线校验和报价，确认后上传、复刻并下载可选试听。
def clone(args):
    check_voice(args.voice_id)
    ref = check_recording(args.recording)
    text = "\n\n".join(paragraphs(args.demo_file)) if args.demo_file else args.demo_text
    if text is not None and not text.strip():
        raise ValueError("试听文本不能为空。")
    out = Path(args.demo_out or f"试听-{args.voice_id}.mp3")
    if out.exists():
        raise ValueError("试听输出已存在，拒绝覆盖。")
    count = billable(text) if text else 0
    say(f"音色名检查通过；音色 ¥9.90（首次正式合成时扣）＋试听{price(count)}；"
        f"合计约 ¥{9.9 + count * 3.5 / 10000:.2f}")
    if not args.yes:
        return
    load_key(args.key_file)
    boundary = uuid.uuid4().hex
    mime = {".mp3": "audio/mpeg", ".wav": "audio/wav", ".m4a": "audio/mp4"}[ref.suffix.lower()]
    body = (f'--{boundary}\r\nContent-Disposition: form-data; name="purpose"\r\n\r\nvoice_clone\r\n'
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="reference{ref.suffix.lower()}"\r\n'
            f'Content-Type: {mime}\r\n\r\n').encode() + ref.read_bytes()
    uploaded = post("/v1/files/upload", body + f"\r\n--{boundary}--\r\n".encode(),
                    f"multipart/form-data; boundary={boundary}")
    payload = {"file_id": uploaded["file"]["file_id"], "voice_id": args.voice_id,
               "need_noise_reduction": True, "need_volume_normalization": True, "language_boost": "Chinese"}
    if text:
        payload.update(text=text, model=MODEL)
    data = post("/v1/voice_clone", payload)
    say(f"复刻成功；input_sensitive={data.get('input_sensitive')}。")
    demo = data.get("demo_audio")
    if demo:
        if not isinstance(demo, str) or not demo.startswith("https://"):
            raise ValueError("试听地址不是 HTTPS，停止下载。")
        audio = fetch(urllib.request.Request(demo))  # 下载不携带 Key。
        with out.open("xb") as handle:
            handle.write(audio)
        say(f"试听已保存：{out}")
    elif text:
        say("接口未返回试听，不重试；请到控制台核对。")


# 验证 WAV 可解码，返回音频参数。
def wav_params(source):
    with wave.open(source, "rb") as audio:
        if audio.getnframes() <= 0:
            raise ValueError("WAV 没有音频帧。")
        return audio.getparams()


# 按顺序拼接并插入静音；有 ffmpeg 时再归一化后原子替换输出。
def concatenate(files, out, gap):
    params = wav_params(str(files[0]))
    with tempfile.TemporaryDirectory(dir=out.parent) as work:
        joined, normalized = Path(work) / "joined.wav", Path(work) / "normalized.wav"
        with wave.open(str(joined), "wb") as target:
            target.setparams(params)
            silence = (b"\x80" if params.sampwidth == 1 else b"\x00" * params.sampwidth)
            silence *= round(gap * params.framerate) * params.nchannels
            for index, file in enumerate(files):
                with wave.open(str(file), "rb") as source:
                    current = source.getparams()
                    if current[:3] != params[:3] or current.comptype != params.comptype:
                        raise ValueError("各段 WAV 格式不一致，无法拼接。")
                    if index:
                        target.writeframes(silence)
                    target.writeframes(source.readframes(source.getnframes()))
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg:
            subprocess.run([ffmpeg, "-v", "error", "-y", "-i", str(joined), "-af",
                            "loudnorm=I=-16:TP=-1.5:LRA=11", "-ar", str(params.framerate),
                            str(normalized)], capture_output=True, check=True, timeout=180)
            normalized.replace(joined)
        else:
            say("未安装 ffmpeg，跳过响度归一化。")
        joined.replace(out)


# 分段合成只提交缺失段，任一段失败立即停止，不重试。
def speak(args):
    check_voice(args.voice_id)
    parts, out = paragraphs(args.script), Path(args.out)
    if out.suffix.lower() != ".wav":
        raise ValueError("--out 必须是 .wav 文件。")
    if not math.isfinite(args.speed) or not 0.5 <= args.speed <= 2:
        raise ValueError("--speed 须为 0.5–2。")
    if not math.isfinite(args.gap) or args.gap < 0:
        raise ValueError("--gap 须为有限非负数。")
    directory = out.with_name(out.stem + "_段落")
    files = [directory / f"{i:02d}.wav" for i in range(1, len(parts) + 1)]
    pending = [(text, file) for text, file in zip(parts, files) if not file.exists()]
    say(f"稿子共 {len(parts)} 段，待合成 {len(pending)} 段：{price(sum(billable(t) for t, _ in pending))}")
    if args.model != MODEL:
        say(f"报价仅按 {MODEL} 的 ¥3.5/万字符参考；所选模型单价未核实。")
    if not args.yes:
        return
    if pending:
        load_key(args.key_file)
    directory.mkdir(parents=True, exist_ok=True)
    total, unknown = 0, False
    for text, file in zip(parts, files):
        if file.exists():
            wav_params(str(file))
            say(f"跳过已有段：{file.name}")
            continue
        data = post("/v1/t2a_v2", {"model": args.model, "text": text, "stream": False,
                    "voice_setting": {"voice_id": args.voice_id, "speed": args.speed, "vol": 1, "pitch": 0},
                    "audio_setting": {"sample_rate": 44100, "format": "wav", "channel": 1}})
        usage = data.get("extra_info", {}).get("usage_characters")
        if type(usage) is int and usage >= 0:
            total += usage
        else:
            unknown = True
        say(f"接口成功：{file.name}，实际计费字符={usage if type(usage) is int else '未知'}")
        audio = bytes.fromhex(data["data"]["audio"])
        wav_params(io.BytesIO(audio))
        with file.open("xb") as handle:
            handle.write(audio)
    concatenate(files, out, args.gap)
    amount = f"约 ¥{total * 3.5 / 10000:.2f}" if not unknown and args.model == MODEL else "未知，请核对账单"
    say(f"本次实际计费字符合计={'未知（已知部分=' + str(total) + '）' if unknown else total}，金额 {amount}（不含音色费）。")


# 构建三个子命令；Key 参数只在付费分支使用。
def build_parser():
    parser = Parser(description=__doc__)
    subs = parser.add_subparsers(dest="command", required=True)
    quote = subs.add_parser("quote")
    quote.add_argument("script")
    cl = subs.add_parser("clone")
    cl.add_argument("recording")
    cl.add_argument("voice_id")
    demo = cl.add_mutually_exclusive_group()
    demo.add_argument("--demo-text")
    demo.add_argument("--demo-file")
    cl.add_argument("--demo-out")
    sp = subs.add_parser("speak")
    sp.add_argument("script")
    sp.add_argument("voice_id")
    sp.add_argument("--out", default="成品.wav")
    sp.add_argument("--speed", type=float, default=1.15)
    sp.add_argument("--model", default=MODEL)
    sp.add_argument("--gap", type=float, default=0.55)
    for paid in (cl, sp):
        paid.add_argument("--yes", action="store_true", help="确认付费并联网；默认只报价")
        paid.add_argument("--key-file", default="~/.config/minimax/api_key")
    return parser


# 主入口拦住异常，不打印 traceback 或底层错误中的敏感信息。
def main(argv=None):
    global KEY
    KEY = ""
    try:
        args = build_parser().parse_args(argv)
        if args.command == "quote":
            counts = [billable(p) for p in paragraphs(args.script)]
            for index, count in enumerate(counts, 1):
                say(f"第 {index} 段：{price(count)}")
            say("合计：" + price(sum(counts)))
        else:
            (clone if args.command == "clone" else speak)(args)
        return 0
    except KeyboardInterrupt:
        say("已中断；" + BILLING_NOTICE, error=True)
        return 130
    except Exception as exc:  # noqa: BLE001 - CLI 边界禁止 traceback 泄露凭证。
        reason = str(exc) if isinstance(exc, ValueError) else "文件、音频或接口数据处理失败，请检查输入和工具安装。"
        say("错误：" + reason + "；" + BILLING_NOTICE, error=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
