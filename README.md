# DAILY-ISSUE

GitHub Actions로 돌아가는 개인 알림 자동화 모음입니다.

| 워크플로 | 하는 일 | 실행 시점 |
| --- | --- | --- |
| `.github/workflows/kakao-briefing.yml` | `briefing.txt` 내용을 카카오톡으로 전송 | `briefing.txt`가 main에 push될 때 |
| `.github/workflows/youtube-kakao.yml` | 유튜브 채널 새 영상을 요약해 카카오톡으로 전송 | 매시 정각 + 수동 실행 |

---

## 유튜브 새 영상 → 카카오톡 루틴

대상 채널: **소수몽키** ([@sosumonkey](https://www.youtube.com/@sosumonkey), `UCC3yfxS5qC6PCwDzetUuEWg`)

### 동작 방식

1. 매시 정각, 유튜브 RSS 피드(`feeds/videos.xml`)에서 최근 영상 목록을 가져옵니다. **유튜브 API 키가 필요 없습니다.**
2. `data/youtube_seen.json`에 기록된 영상 ID와 비교해 새 영상만 골라냅니다.
3. 제목과 설명을 Claude로 1~2문장 요약합니다. (`ANTHROPIC_API_KEY`가 없으면 영상 설명 앞부분을 그대로 사용)
4. 카카오톡 '나에게 보내기'로 전송합니다. 메시지에는 제목, 업로드 시각(KST), 요약, 영상 링크가 들어가고 '영상 보기' 버튼이 붙습니다.
5. 보낸 영상 ID를 `data/youtube_seen.json`에 커밋해 다음 실행 때 중복 전송을 막습니다.

전송 예시:

```
🎥 소수몽키 새 영상 (08/17 20:00)
연준 회의록 공개! 시장이 놓친 한 문장

연준 회의록에서 시장이 지나친 문장을 짚어보고, 다음 회의까지 체크할 일정들을 정리합니다.

https://youtu.be/XXXXXXXXXXX
```

### 필요한 Secrets

`Settings → Secrets and variables → Actions`에 등록합니다.

| 이름 | 필수 | 설명 |
| --- | --- | --- |
| `KAKAO_CLIENT_ID` | ✅ | 카카오 앱의 REST API 키 (기존 브리핑 워크플로와 동일) |
| `KAKAO_CLIENT_SECRET` | ✅ | 카카오 앱 Client Secret |
| `KAKAO_REFRESH_TOKEN` | ✅ | `talk_message` 동의를 받은 리프레시 토큰 |
| `ANTHROPIC_API_KEY` | 선택 | Claude 요약용. 없으면 영상 설명으로 대체됩니다 |

### 첫 실행

**첫 실행에서는 알림을 보내지 않습니다.** 지금 피드에 있는 영상 15개를 "이미 본 것"으로 기록만 하고, 그 다음 실행부터 새로 올라온 영상만 보냅니다. (설정 직후 과거 영상이 한꺼번에 오는 걸 막기 위한 동작입니다.)

`Actions → YouTube to KakaoTalk → Run workflow`로 한 번 수동 실행해 초기화해두는 것을 권합니다.

### 조정할 수 있는 값

워크플로의 `env`로 바꿀 수 있습니다.

| 환경변수 | 기본값 | 설명 |
| --- | --- | --- |
| `YOUTUBE_CHANNEL_ID` | `UCC3yfxS5qC6PCwDzetUuEWg` | 감시할 채널 ID |
| `YOUTUBE_HANDLE` | `sosumonkey` | 채널 ID로 피드 조회가 실패하면 이 핸들로 ID를 다시 찾습니다 |
| `YOUTUBE_MAX_NOTIFY` | `5` | 한 번 실행에 보낼 최대 개수 (도배 방지) |
| `YOUTUBE_STATE_FILE` | `data/youtube_seen.json` | 전송 기록 파일 경로 |

주기를 바꾸려면 워크플로의 `cron` 값을 수정하세요 (`"*/30 * * * *"` = 30분마다). GitHub Actions의 스케줄은 몇 분 지연될 수 있습니다.

### 알아둘 점

- 스케줄 워크플로는 **기본 브랜치(main)에 있어야** 동작합니다. 브랜치에 머지하기 전에는 자동 실행되지 않습니다.
- 카카오 리프레시 토큰은 유효기간이 있습니다(약 60일, 사용 시 자동 갱신). 만료되면 토큰을 다시 발급받아 Secret을 갱신해야 합니다.
- 카카오 '나에게 보내기' 텍스트 템플릿은 200자 제한이 있어, 요약 길이를 제목 길이에 맞춰 자동으로 조절합니다.
- 유튜브 RSS에는 쇼츠와 예정된 라이브도 함께 올라옵니다. 이것들을 빼고 싶으면 알려주세요.
