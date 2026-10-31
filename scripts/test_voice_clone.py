"""离线测试：所有网络、外部工具和 Key 都用假数据。"""
import io
import json
import shutil
import urllib.error
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

import voice_clone as vc

FIRST = "都说，对人抱有期待，是一场微妙的<#0.2#>暴力。<#0.6#>我最近在带学生，才真的体会到这句话。"
SECOND = "第二段。"
VOICE = "MyVoice2026"
FAKE_KEY = "FAKE_PRIVATE_CREDENTIAL_123"


# 默认禁止联网、读取真实环境凭证、运行外部程序，并隔离当前目录。
@pytest.fixture(autouse=True)
def offline(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(vc, "KEY", "")
    monkeypatch.setattr(vc.shutil, "which", lambda name: None)

    # 任何漏打桩的副作用都使测试失败。
    def forbidden(*args, **kwargs):
        pytest.fail("意外调用真实网络或外部程序")

    monkeypatch.setattr(vc.urllib.request, "urlopen", forbidden)
    monkeypatch.setattr(vc.subprocess, "run", forbidden)


# 准备临时稿子、录音和假 Key 文件。
@pytest.fixture
def inputs(tmp_path):
    script = tmp_path / "稿子.txt"
    script.write_text(f"  {FIRST}  \n\n \n  {SECOND} \n", encoding="utf-8")
    recording = tmp_path / "录音.mp3"
    recording.write_bytes(b"fake recording")
    key = tmp_path / "fake-key"
    key.write_text(FAKE_KEY)
    return SimpleNamespace(script=script, recording=recording, key=key,
                           out=tmp_path / "成品.wav", directory=tmp_path / "成品_段落")


# 用 wave 生成可解码的单声道 PCM WAV。
def wav_bytes(seconds=0.1, rate=44100):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setparams((1, 2, rate, 0, "NONE", "not compressed"))
        audio.writeframes(b"\x00\x00" * round(seconds * rate))
    return buffer.getvalue()


# 伪造业务成功的接口响应。
def response(**payload):
    return {"base_resp": {"status_code": 0}, **payload}


# 构造一次合成的假返回值。
def synthesis(usage=7, seconds=0.1):
    return response(data={"audio": wav_bytes(seconds).hex()},
                    extra_info={"usage_characters": usage})


# 安装按次序响应的 urlopen 桩，记录请求以检查次数和请求体。
def stub_network(monkeypatch, results):
    calls = []
    iterator = iter(results)

    # 只返回预先提供的数据，不发出任何真实请求。
    def urlopen(request, timeout):
        calls.append(request)
        assert timeout == 180
        result = next(iterator)
        if isinstance(result, Exception):
            raise result
        raw = json.dumps(result).encode() if isinstance(result, dict) else result
        return io.BytesIO(raw)

    monkeypatch.setattr(vc.urllib.request, "urlopen", urlopen)
    return calls


# 构建使用假凭证的合成命令。
def speak_args(inputs, *extra):
    return ["speak", str(inputs.script), VOICE, "--out", str(inputs.out),
            "--key-file", str(inputs.key), *extra]


# 构建使用假凭证的克隆命令。
def clone_args(inputs, *extra):
    return ["clone", str(inputs.recording), VOICE, "--key-file", str(inputs.key), *extra]


# 两个实际扣费样例必须严格定标为 83、7，合计 90。
def test_billing_and_quote(inputs, capsys):
    assert vc.billable(" \n" + FIRST + " \n") == 83
    assert vc.billable("  " + SECOND + " ") == 7
    assert vc.main(["quote", str(inputs.script)]) == 0
    output = capsys.readouterr().out
    assert "第 1 段：计费字符=83，约 ¥0.03" in output
    assert "第 2 段：计费字符=7，约 ¥0.00" in output
    assert "合计：计费字符=90，约 ¥0.03" in output


# 三个子命令在未确认时不加载 Key、不碰 Key 文件、不联网、不新建输出。
@pytest.mark.parametrize("command", ["quote", "clone", "speak"])
def test_dry_run_has_no_side_effects(command, inputs, monkeypatch):
    original = Path.read_text

    # 仅允许读稿子，任何 Key 文件读取均失败。
    def guarded_read(path, *args, **kwargs):
        assert path == inputs.script, "未确认付费却读取 Key 文件"
        return original(path, *args, **kwargs)

    # 未确认时甚至不能调用 Key 加载函数。
    def forbidden_load(*args):
        pytest.fail("未确认付费却尝试加载 Key")

    monkeypatch.setattr(Path, "read_text", guarded_read)
    monkeypatch.setattr(vc, "load_key", forbidden_load)
    before = set(inputs.out.parent.iterdir())
    args = {"quote": ["quote", str(inputs.script)],
            "clone": clone_args(inputs), "speak": speak_args(inputs)}[command]
    assert vc.main(args) == 0
    assert set(inputs.out.parent.iterdir()) == before


# 已有第一段不提交；只合成第二段，按 API 实际字符记账并插入静音。
def test_speak_skips_existing_and_concatenates(inputs, monkeypatch, capsys):
    inputs.directory.mkdir()
    first = wav_bytes(0.2)
    (inputs.directory / "01.wav").write_bytes(first)
    calls = stub_network(monkeypatch, [synthesis(usage=19, seconds=0.3)])
    assert vc.main(speak_args(inputs, "--gap", "0.55", "--yes")) == 0
    assert len(calls) == 1
    request = calls[0]
    assert request.full_url == vc.HOST + "/v1/t2a_v2"
    assert request.get_header("Authorization") == "Bearer " + FAKE_KEY
    body = json.loads(request.data)
    assert body == {"model": vc.MODEL, "text": SECOND, "stream": False,
                    "voice_setting": {"voice_id": VOICE, "speed": 1.15, "vol": 1, "pitch": 0},
                    "audio_setting": {"sample_rate": 44100, "format": "wav", "channel": 1}}
    assert (inputs.directory / "01.wav").read_bytes() == first
    assert (inputs.directory / "02.wav").is_file()
    with wave.open(str(inputs.out), "rb") as audio:
        assert audio.getnframes() / audio.getframerate() == pytest.approx(0.2 + 0.3 + 0.55)
    output = capsys.readouterr().out
    assert "跳过已有段：01.wav" in output
    assert "本次实际计费字符合计=19" in output
    assert "未安装 ffmpeg" in output


# 三段全部生成时拼接两个间隔，第二次重跑不读凭证也不重复付费。
def test_three_segments_and_resume_without_key(inputs, monkeypatch, capsys):
    inputs.script.write_text("甲。\n\n乙。\n\n丙。", encoding="utf-8")
    calls = stub_network(monkeypatch, [synthesis(3, 0.1), synthesis(3, 0.2), synthesis(3, 0.3)])
    assert vc.main(speak_args(inputs, "--gap", "0.25", "--yes")) == 0
    with wave.open(str(inputs.out), "rb") as audio:
        assert audio.getnframes() / audio.getframerate() == pytest.approx(0.6 + 2 * 0.25)
    assert len(calls) == 3
    inputs.key.unlink()
    assert vc.main(speak_args(inputs, "--gap", "0.25", "--yes")) == 0
    assert len(calls) == 3
    assert "本次实际计费字符合计=0" in capsys.readouterr().out


# 第二段业务失败后第三段绝不能提交，第一段文件保留方便续跑。
def test_second_failure_stops_third(inputs, monkeypatch, capsys):
    inputs.script.write_text("第一。\n\n第二。\n\n第三。", encoding="utf-8")
    calls = stub_network(monkeypatch, [synthesis(), {"base_resp": {
        "status_code": 9999, "status_msg": f"余额不足 {FAKE_KEY} sk-another-secret"}}])
    assert vc.main(speak_args(inputs, "--yes")) == 1
    assert len(calls) == 2
    assert (inputs.directory / "01.wav").is_file()
    assert not (inputs.directory / "02.wav").exists()
    assert not (inputs.directory / "03.wav").exists()
    assert not inputs.out.exists()
    captured = capsys.readouterr()
    assert "余额不足" in captured.err
    assert "如果已经提交，请到控制台核对扣费" in captured.err
    assert FAKE_KEY not in captured.out + captured.err
    assert "sk-another-secret" not in captured.out + captured.err


# 尾随下划线和大于 20MB 的录音在联网前拒绝。
@pytest.mark.parametrize("invalid", ["trailing_underscore", "oversize"])
def test_clone_rejects_invalid_offline(inputs, invalid, capsys):
    args = clone_args(inputs, "--yes")
    if invalid == "trailing_underscore":
        args[2] = "MyVoice2026_"
        expected = "末尾不能是"
    else:
        with inputs.recording.open("wb") as recording:
            recording.truncate(20 * 1024 * 1024 + 1)
        expected = "20MB"
    assert vc.main(args) == 1
    assert expected in capsys.readouterr().err


# 包月 Key 和未实名的业务错误显示可操作的中文提示。
@pytest.mark.parametrize("code, expected", [
    (2061, "这把 Key 是包月套餐的，克隆要用控制台『API Key』页新建的按量付费 Key"),
    (2038, "账号没实名认证：控制台左上角点空间名 → 管理我的空间 → 管理 → 空间认证"),
])
def test_friendly_api_errors(code, expected, inputs, monkeypatch, capsys):
    calls = stub_network(monkeypatch, [response(file={"file_id": 123}),
                                      {"base_resp": {"status_code": code, "status_msg": FAKE_KEY}}])
    assert vc.main(clone_args(inputs, "--yes")) == 1
    assert len(calls) == 2
    captured = capsys.readouterr()
    assert expected in captured.err
    assert FAKE_KEY not in captured.out + captured.err


# 完整克隆流程包含 multipart、复刻请求和无认证头的 HTTPS 试听下载。
@pytest.mark.parametrize("demo_source", ["--demo-text", "--demo-file"])
def test_clone_upload_and_demo(inputs, demo_source, monkeypatch, capsys):
    calls = stub_network(monkeypatch, [response(file={"file_id": 123}),
                                      response(demo_audio="https://example.invalid/demo.mp3",
                                               input_sensitive=False), b"fake demo audio"])
    value = FIRST if demo_source == "--demo-text" else str(inputs.script)
    demo_out = inputs.out.parent / "试听.mp3"
    assert vc.main(clone_args(inputs, demo_source, value, "--demo-out", str(demo_out), "--yes")) == 0
    assert len(calls) == 3
    assert calls[0].full_url.endswith("/v1/files/upload")
    assert b'name="purpose"\r\n\r\nvoice_clone' in calls[0].data
    assert b'name="file"' in calls[0].data
    assert inputs.recording.read_bytes() in calls[0].data
    payload = json.loads(calls[1].data)
    assert payload == {"file_id": 123, "voice_id": VOICE, "need_noise_reduction": True,
                       "need_volume_normalization": True, "language_boost": "Chinese",
                       "text": FIRST if demo_source == "--demo-text" else FIRST + "\n\n" + SECOND,
                       "model": vc.MODEL}
    assert calls[2].get_header("Authorization") is None
    assert demo_out.read_bytes() == b"fake demo audio"
    captured = capsys.readouterr()
    assert FAKE_KEY not in captured.out + captured.err


# 默认试听名正确，已有试听必须在上传前拒绝覆盖。
def test_demo_default_and_overwrite_refused(inputs, monkeypatch, capsys):
    calls = stub_network(monkeypatch, [response(file={"file_id": 123}),
                                      response(demo_audio="https://example.invalid/demo"), b"demo"])
    assert vc.main(clone_args(inputs, "--demo-text", SECOND, "--yes")) == 0
    demo = Path(f"试听-{VOICE}.mp3")
    assert demo.read_bytes() == b"demo"
    assert vc.main(clone_args(inputs, "--demo-text", SECOND, "--yes")) == 1
    assert len(calls) == 3
    assert "拒绝覆盖" in capsys.readouterr().err


# 环境变量优先于 Key 文件，任意 Key 值和 sk- 字串都不泄露。
def test_environment_key_precedence_and_redaction(inputs, monkeypatch, capsys):
    monkeypatch.setenv("MINIMAX_API_KEY", FAKE_KEY)
    inputs.key.unlink()
    calls = stub_network(monkeypatch, [{"base_resp": {
        "status_code": 999, "status_msg": FAKE_KEY + " sk-synthetic-other-key"}}])
    assert vc.main(clone_args(inputs, "--yes")) == 1
    assert calls[0].get_header("Authorization") == "Bearer " + FAKE_KEY
    captured = capsys.readouterr()
    assert FAKE_KEY not in captured.out + captured.err
    assert "sk-synthetic-other-key" not in captured.out + captured.err


# HTTP、网络错误和无效 JSON 都只请求一次，提示核对扣费且不泄露错误原文。
@pytest.mark.parametrize("error", [
    urllib.error.HTTPError("https://example.invalid", 403, FAKE_KEY, {}, None),
    urllib.error.URLError(FAKE_KEY), TimeoutError(FAKE_KEY), b"invalid json",
], ids=["http", "network", "timeout", "invalid-json"])
def test_network_failures_no_retry(error, inputs, monkeypatch, capsys):
    calls = stub_network(monkeypatch, [error])
    assert vc.main(speak_args(inputs, "--yes")) == 1
    assert len(calls) == 1
    captured = capsys.readouterr()
    assert "如果已经提交，请到控制台核对扣费" in captured.err
    assert FAKE_KEY not in captured.out + captured.err


# 安装了 ffprobe 时检查 10–300 秒的上下限，外部命令同样打桩。
@pytest.mark.parametrize("duration, expected", [(9.9, 1), (10, 0), (300, 0), (300.1, 1), ("nan", 1)])
def test_ffprobe_duration(inputs, monkeypatch, duration, expected):
    monkeypatch.setattr(vc.shutil, "which", lambda name: "/fake/ffprobe")

    # 返回假时长，不运行 ffprobe。
    def run(command, **kwargs):
        assert command[0] == "/fake/ffprobe"
        assert kwargs["timeout"] == 30
        return SimpleNamespace(stdout=str(duration))

    monkeypatch.setattr(vc.subprocess, "run", run)
    assert vc.main(clone_args(inputs)) == expected


# 缺失计费字段不冒充估算值，实际账单显示未知。
def test_missing_usage_is_unknown(inputs, monkeypatch, capsys):
    calls = stub_network(monkeypatch, [synthesis(None), synthesis(7)])
    assert vc.main(speak_args(inputs, "--yes")) == 0
    assert len(calls) == 2
    assert "实际计费字符合计=未知（已知部分=7）" in capsys.readouterr().out


# ffmpeg 归一化使用指定滤镜并保留临时目录之外的最终结果。
def test_ffmpeg_normalization_is_mocked(inputs, monkeypatch):
    stub_network(monkeypatch, [synthesis(), synthesis()])
    monkeypatch.setattr(vc.shutil, "which", lambda name: "/fake/ffmpeg" if name == "ffmpeg" else None)
    commands = []

    # 模拟归一化，实际只复制测试 WAV。
    def run(command, **kwargs):
        commands.append(command)
        assert "loudnorm=I=-16:TP=-1.5:LRA=11" in command
        assert kwargs["timeout"] == 180
        shutil.copyfile(command[command.index("-i") + 1], command[-1])

    monkeypatch.setattr(vc.subprocess, "run", run)
    assert vc.main(speak_args(inputs, "--yes")) == 0
    assert len(commands) == 1
    assert inputs.out.is_file()
    assert not list(inputs.out.parent.glob("tmp*"))


# 参数解析失败也不能把误传的 sk- 凭证打印出来。
def test_argument_errors_are_redacted(capsys):
    assert vc.main(["speak", "x.txt", VOICE, "--speed", "sk-not-a-speed"]) == 1
    captured = capsys.readouterr()
    assert "sk-not-a-speed" not in captured.out + captured.err
