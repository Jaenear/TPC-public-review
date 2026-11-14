# TPC public review

TPC 강의·학습·Q/A·커뮤니티 기능을 외부에서 검토하기 위한 독립 실행본입니다.
원본 개발 작업 폴더, 기존 회원 DB, 운영 계정, 결제 키와 분리되어 있습니다.
Python 표준 라이브러리와 브라우저 ES 모듈을 사용합니다.

## 실행

Python 3.11 이상에서 실행합니다. 별도 패키지 설치는 필요하지 않습니다.

```sh
python3 deployment/serve_review.py --origin http://localhost:8025
```

`http://localhost:8025`로 접속합니다. 최초 실행은 새 schema-33 DB와 무료 프리플랍 MP4를 준비합니다.
회원·관리자 계정은 기본 생성하지 않습니다. 방문자는 테스트 계정을 직접 만들 수 있습니다.
검토 데이터는 Git에서 제외된 `review-data/`에 유지됩니다.

## HTTPS 외부 미리보기

Cloudflare의 공식 `cloudflared`를 설치하고 다음 명령으로 임시 주소를 받습니다.

```sh
cloudflared tunnel --url http://127.0.0.1:8025
```

출력된 실제 `https://….trycloudflare.com` 주소를 `--origin`으로 지정해 서버를 실행합니다.
브라우저의 주소·Host·Origin이 동일해야 로그인과 저장이 동작합니다.
이미 실행 중인 로컬 검토 서버가 있다면 먼저 종료합니다.

```sh
python3 deployment/serve_review.py --origin https://YOUR-ACTUAL-ADDRESS.trycloudflare.com
```

임시 링크는 Mac과 두 프로세스가 실행되는 동안 접속할 수 있으며 재시작 시 주소가 바뀔 수 있습니다.
상시 호스팅이나 운영 배포 완료를 의미하지 않습니다.
[Cloudflare Quick Tunnels 공식 문서](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/)

## 검토 범위

- 무료 강의와 자체 제작 MP4, 회원별 시청 위치·진도 저장, 강의 Q/A, 커뮤니티, 수강 관리
- 기본 관리자 없음. 운영자 생성과 코치 권한 부여는 별도 운영 절차입니다.
- 토스 상점 키·실제 결제·외부 이메일 발송은 연결하지 않습니다. 솔버는 후속 개발 범위입니다.
- 기존 DB의 소유 표식·스키마가 맞지 않으면 실행을 중단하며 자동 이관하지 않습니다.
- 본문·API·보호된 미디어만 서버의 기존 허용 목록으로 제공합니다. Git 폴더와 DB는 HTTP로 제공하지 않습니다.

애플리케이션 코드는 전체 검증을 통과했던 소스
`4032242a65ddfc82f07e6bea932809cb8d34a1dabe539b84b5df22f194153465`의 schema-33 버전입니다.
원본의 미완료 schema-34 개발 변경은 포함하지 않았습니다.
추가 실행 보호 장치는 `deployment/serve_review.py`에 있습니다.

상시 서비스로 전환할 때는 Git 배포와 함께 HTTPS 도메인, 영속 DB·미디어 볼륨,
백업·복구, 실제 상점 테스트, 이메일 발송, 관리자 인증을 별도로 검증해야 합니다.
정적 GitHub Pages만으로 Python API와 SQLite 저장을 실행할 수는 없습니다.
