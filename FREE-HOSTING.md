# TPC 무료 클라우드 배포

PythonAnywhere **Beginner ($0/month)**에 Python/SQLite 앱을 실행하는 구성입니다.
Mac·로컬 터널을 사용하지 않습니다. 새 검토용 회원 DB를 호스팅 계정의 비공개 디스크에 저장합니다.
무료 요금제는 512MiB 저장 공간, 웹앱 1개·worker 1개, 매월 웹앱 연장 조건이 있습니다.
[요금제](https://www.pythonanywhere.com/pricing/) · [무료 계정 제한](https://help.pythonanywhere.com/pages/FreeAccountsFeatures/)

## Git에서 가져오기

PythonAnywhere의 Bash console에서 실행합니다. 기존 폴더가 있다면 덮어쓰지 말고 현재 배포를 먼저 확인합니다.

```sh
git clone --branch public-review --single-branch https://github.com/Jaenear/TPC-public-review.git ~/TPC-public-review
cd ~/TPC-public-review
python3 -m unittest deployment.test_wsgi -v
```

추가 Python 패키지는 필요하지 않습니다. 샘플 MP4는 자체 제작 파일입니다.
테스트는 임시 DB만 사용합니다. 기존 회원·운영 DB를 업로드하거나 Git에 커밋하지 않습니다.

## Web 탭 설정

1. 무료 웹앱을 추가하고 **Manual configuration**과 지원되는 Python 버전을 선택합니다.
2. Code의 source directory는 `/home/YOUR_USERNAME/TPC-public-review`로 설정합니다.
3. WSGI configuration 파일에 `deployment/pythonanywhere_wsgi.py.example` 내용을 넣고
   `YOUR_USERNAME` 두 곳을 실제 계정명으로 교체합니다.
4. **Force HTTPS**를 켜고 Reload합니다.
5. 실제 `https://YOUR_USERNAME.pythonanywhere.com` 주소에서 강의·로그인·저장을 확인합니다.

루트 폴더, `review-data/`, `.git/`, `.private/`, `server/`, `deployment/`를 static mapping으로 연결하지 않습니다.
기본은 static mapping을 추가하지 않고 TPC의 기존 공개 파일 허용 목록을 사용합니다.

## 데이터와 업데이트

최초 실행은 `review-data/review.sqlite3`(schema 33), 소유 표식, 샘플 영상만 생성합니다.
기본 회원·관리자·공유 비밀번호는 만들지 않습니다. 계정·진도·게시글은 호스팅 계정의 디스크에 유지됩니다.
재시작은 같은 DB·샘플 asset·차시 ID를 사용합니다. DB와 업로드 폴더는 Git에서 제외됩니다.

```sh
cd ~/TPC-public-review
git pull --ff-only origin public-review
```

코드를 갱신한 뒤 Web 탭에서 Reload합니다. 스키마가 다르거나 DB 소유 표식이 없으면 런처가 시작을 거부합니다.
새 스키마 배포 전에는 기존 DB와 미디어의 비공개 백업·복구 검증을 먼저 완료해야 합니다.
원본의 미완료 schema34 변경은 이 검토 버전에 포함하지 않습니다.

실제 결제 키·이메일 발송·솔버는 연결하지 않습니다. 무료 계정의 외부 통신 제한도 별도 확인이 필요합니다.
관리자 계정 생성과 실 상점 운영은 별도 운영 절차로 진행합니다.

## 실행 확인

- 가입 → 무료 프리플랍 등록 → MP4 재생 → 24초 저장 → 재로그인/웹앱 Reload → 같은 위치 확인
- 다른 회원의 진도·비공개 질문 격리, 작성자 외 게시글 수정 거부, 관리자 메뉴 거부
- `/review-data/review.sqlite3`, `/.git/config`, `/.env`, 서버·배포 Python 파일의 HTTP 제공 거부
- PythonAnywhere에서 실제 웹앱이 생성·Reload되고 HTTPS 응답이 확인된 후에만 원격 배포 완료로 기록

Git 커밋·로컬 테스트 통과는 호스팅 계정 로그인과 실제 원격 배포를 대신하지 않습니다.
