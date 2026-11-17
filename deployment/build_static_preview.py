"""Export an anonymous, read-only TPC preview for static hosts, including Pages.

The only database ever opened is freshly created in a temporary directory.
Nothing is copied from the working member DB, report, environment or logs.
"""
import argparse
import hashlib
import io
import json
from pathlib import Path
import re
import shutil
import sys
import tempfile
from urllib.parse import urlsplit, parse_qsl, urlencode
from wsgiref.util import setup_testing_defaults

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from deployment.wsgi import make_application

ORIGIN = "https://tpc-static-preview.example"
SAMPLE = ROOT / "review-media/preflop-sample.mp4"
TEXT_SUFFIXES = {".js", ".mjs", ".html", ".css", ".svg"}


def route_key(path):
    parsed = urlsplit(path)
    query = urlencode(sorted(parse_qsl(parsed.query, keep_blank_values=True)))
    return parsed.path + ("?" + query if query else "")


def public_get(app, path):
    parsed = urlsplit(path)
    environ = {}
    setup_testing_defaults(environ)
    environ.update(REQUEST_METHOD="GET", PATH_INFO=parsed.path,
                   QUERY_STRING=parsed.query, HTTP_HOST="tpc-static-preview.example",
                   **{"wsgi.url_scheme": "https", "wsgi.input": io.BytesIO()})
    result = {}
    def start_response(status, headers):
        result.update(status=int(status.split()[0]), headers=dict(headers))
    payload = b"".join(app(environ, start_response))
    if not result["headers"].get("Content-Type", "").startswith("application/json"):
        raise ValueError("Only anonymous JSON routes may be exported: " + path)
    return {"status": result["status"], "body": json.loads(payload)}


def snapshot():
    with tempfile.TemporaryDirectory(prefix="tpc-static-export-") as temp:
        app = make_application(ORIGIN, Path(temp) / "fresh-data")
        routes = {}
        def collect(path):
            response = public_get(app, path)
            routes[route_key(path)] = response
            return response["body"]
        collect("/api/session")
        catalog = collect("/api/catalog")
        for path in ("/api/practice/questions", "/api/qa/questions",
                     "/api/coaching/products", "/api/checkout/config"):
            collect(path)
        for mode in ("test", "live"):
            collect("/api/policies/current?mode=" + mode)
            collect("/api/policies/current?mode=" + mode + "&context=account")
        board_data = collect("/api/community/posts?board=general")
        for board in board_data["boards"]:
            collect("/api/community/posts?board=" + board["id"])
        for coach in catalog["coaches"]:
            collect("/api/coaches/" + coach["id"])
        for course in catalog["courses"]:
            collect("/api/courses/" + course["id"] + "/start")
            for lesson in course["lessons"]:
                if lesson["free"]:
                    collect("/api/courses/" + course["id"] + "/lessons/" + lesson["id"])
        with app.tpc.db.connect() as connection:
            for table in ("users", "sessions", "questions", "posts"):
                if connection.execute("SELECT count(*) FROM " + table).fetchone()[0]:
                    raise ValueError("Static export requires an empty private database: " + table)
        original = routes["/api/courses/preflop-foundations/lessons/position"]["body"]["lesson"]["videoAssetId"]
        sample_hash = hashlib.sha256(SAMPLE.read_bytes()).hexdigest()
        sample_id = "asset_" + sample_hash[:32]
        serialized = json.dumps(routes, ensure_ascii=False).replace(original, sample_id)
        return {"format": 1, "mode": "static-read-only", "schema": 33,
                "sampleId": sample_id, "sampleSha256": sample_hash,
                "routes": json.loads(serialized)}


def validate_base(base):
    if not re.fullmatch(r"/(?:[A-Za-z0-9_-]+/)*", base):
        raise ValueError("Base must be / or a safe path such as /TPC-public-review/.")
    return base


def rebase(text, base):
    # Explicit application asset namespaces only; never rewrite API URLs or URLs
    # beginning //, and never touch the original server-served source files.
    return re.sub(r'([\x22\x27`(])/(assets/|src/|proposal/|styles\.css)',
                  lambda m: m.group(1) + base + m.group(2), text)


def build(destination, base="/"):
    base = validate_base(base)
    destination = Path(destination).resolve()
    if destination == ROOT or ROOT in destination.parents and destination.name not in ("_site", "dist"):
        raise ValueError("Choose a separate output directory, _site or dist.")
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("Output must be empty; the exporter never deletes existing files.")
    manifest = snapshot()
    destination.mkdir(parents=True, exist_ok=True)
    for name in ("index.html", "styles.css"):
        (destination / name).write_text(rebase((ROOT / name).read_text(), base))
    for directory, suffixes in (("src", {".js", ".css"}),
                                ("assets", {".svg", ".png", ".jpg", ".jpeg", ".webp"}),
                                ("proposal", {".html", ".css", ".js"})):
        for source in (ROOT / directory).rglob("*"):
            if not source.is_file() or source.is_symlink() or source.suffix not in suffixes:
                continue
            target = destination / source.relative_to(ROOT)
            target.parent.mkdir(parents=True, exist_ok=True)
            if source.suffix in TEXT_SUFFIXES:
                target.write_text(rebase(source.read_text(), base))
            else:
                shutil.copyfile(source, target)
    app_path = destination / "src/app.js"
    app_text = app_path.read_text()
    needle = 'function openAuth(mode = "login") {'
    if app_text.count(needle) != 1:
        raise ValueError("Authentication entry point changed; review the static boundary.")
    app_text = 'import { showPreviewInfo } from "./static-preview-api.js";\n' + app_text.replace(
        needle, needle + '\n  return showPreviewInfo();\n')
    identity_entry = '(await renderIdentity(path, params, ctx, identityLinks)) ||'
    if app_text.count(identity_entry) != 1:
        raise ValueError("Identity routing changed; review the static input boundary.")
    app_text = app_text.replace(identity_entry, '''(path.startsWith("/account") ? {html: '<section class="container prose-page"><p class="eyebrow">PUBLIC PREVIEW</p><h1>계정 기능은 서버 연결 후 제공됩니다.</h1><p>공개 미리보기에서는 이메일과 비밀번호를 입력받지 않습니다.</p><a class="btn btn-primary" href="#/library">강의 둘러보기</a></section>'} : null) ||''')
    # Scope the platform description to this actual read-only publication.
    app_text = app_text.replace("이름, 이메일, 학습 진도와 직접 제출한 신청 내용은 계정별로 저장됩니다.",
        "이 공개 미리보기에서는 계정 생성, 개인정보 제출과 회원별 저장을 제공하지 않습니다.")
    app_path.write_text(app_text)
    gate_path = destination / "src/platform-ui.js"
    gate = gate_path.read_text().replace(
        "수강, 질문, 커뮤니티와 신청 내역이 내 계정에 연결됩니다.",
        "회원 전용 기능은 서버 연결 후 이용할 수 있습니다.")
    gate = gate.replace("캠프에 입장해주세요.", "공개 미리보기 이용 안내")
    gate = gate.replace("계정을 만들고 무료 강의부터 시작할 수 있습니다.",
                        "지금은 강의·샘플 영상과 공개 화면을 둘러볼 수 있습니다.")
    gate = gate.replace("로그인 / 회원가입", "이용 범위 확인")
    gate_path.write_text(gate)
    player_path = destination / "src/video-player.js"
    player = player_path.read_text()
    if player.count('/api/media/') != 2:
        raise ValueError("Media entry points changed; review the static media boundary.")
    player = player.replace('/api/media/${e(lesson.videoAssetId)}', base + 'public-media/${e(lesson.videoAssetId)}.mp4')
    player = player.replace('/api/media/${encodeURIComponent(lesson.videoAssetId)}?',
                            base + 'public-media/${encodeURIComponent(lesson.videoAssetId)}.mp4?')
    player = player.replace("로그인하고 무료 강의를 등록하면 이어보기 위치를 저장합니다.",
                            "공개 미리보기에서는 영상을 재생할 수 있습니다. 회원별 이어보기 저장은 서버 연결 후 제공됩니다.")
    player_path.write_text(player)
    (destination / "public-media").mkdir()
    shutil.copyfile(SAMPLE, destination / "public-media" / (manifest["sampleId"] + ".mp4"))
    (destination / "preview-data.json").write_text(json.dumps(manifest, ensure_ascii=False, separators=(",", ":")))
    shutil.copyfile(ROOT / "deployment/static-preview-api.mjs", destination / "src/static-preview-api.js")
    (destination / "src/preview-entry.js").write_text('''import { installStaticPreview, showPreviewInfo } from "./static-preview-api.js";
try {
  const response = await fetch(new URL("../preview-data.json", import.meta.url));
  if (!response.ok) throw new Error("공개 강의 정보를 불러오지 못했습니다.");
  installStaticPreview(await response.json());
  document.querySelector("#preview-details").addEventListener("click", showPreviewInfo);
  await import("./app.js");
} catch (error) {
  document.querySelector("#app").textContent = "미리보기를 불러오지 못했습니다. 페이지를 새로고침해주세요.";
  console.error(error);
}
''')
    html_path = destination / "index.html"
    html = html_path.read_text().replace('src="' + base + 'src/app.js"', 'src="' + base + 'src/preview-entry.js"')
    html = html.replace('<html lang="ko">', '<html lang="ko" data-preview="static">')
    html = html.replace("<body>", '''<body>
    <aside class="preview-notice" aria-label="공개 미리보기 안내"><div><strong>공개 미리보기</strong><span>강의·샘플 영상·학습 도구를 둘러보세요.</span></div><button id="preview-details" type="button">이용 범위 안내 ↗</button></aside>''')
    html = html.replace("</head>", '''<meta name="robots" content="noindex,nofollow,noarchive"/>
<style>.preview-notice{display:flex;justify-content:space-between;gap:12px;align-items:center;padding:9px max(20px,calc((100vw - 1280px)/2));background:#12332b;color:#c0d9d0;font:12px/1.6 sans-serif;border-bottom:1px solid #315248}.preview-notice div{display:flex;gap:12px;flex-wrap:wrap}.preview-notice strong{color:#72e8ba;white-space:nowrap}.preview-notice button{background:transparent;color:#d8eee5;border:0;cursor:pointer;font:inherit;white-space:nowrap}.preview-notice button:focus-visible{outline:2px solid #72e8ba;outline-offset:3px}.preview-info{display:grid;gap:18px}.preview-info p{color:#acb9b2;line-height:1.8}.preview-info h2{font-size:26px;line-height:1.4}@media(max-width:600px){.preview-notice span{display:none}}</style>
</head>''')
    html_path.write_text(html)
    (destination / ".nojekyll").touch()
    return {"base": base, "routes": len(manifest["routes"]),
            "sampleSha256": manifest["sampleSha256"],
            "files": sum(p.is_file() for p in destination.rglob("*"))}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument("--base", default="/")
    args = parser.parse_args()
    print(json.dumps(build(args.output, args.base), ensure_ascii=False))
