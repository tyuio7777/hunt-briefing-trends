# Hunt Briefing Trends

Hunt Briefing의 **플레이 기회 / 최근 화제 / 접속 추이** 전용 공개 데이터입니다.
기존 `hunt-briefing-feed`는 읽기만 합니다. 방명록 저장소, 기존 앱 푸시, GPT 브리핑 생성·검증·공식 감시 예약은 변경하지 않습니다.

## 수집과 비용 범위

- GitHub Actions `Collect Hunt trends`: 매시 **13분·43분 UTC**를 목표로 실행합니다(한국시간도 분은 동일).
- Steam 앱 594650의 공식 `GetNumberOfCurrentPlayers/v1` API에서 현재 인원 1건을 수집합니다. API 키나 이용자 로그인은 필요하지 않습니다.
- 공개 저장소의 표준 GitHub-hosted Ubuntu runner와 Python 표준 라이브러리만 사용합니다. 별도 서버, 유료 API/AI, 유료 runner, LFS, artifact 업로드는 사용하지 않습니다.
- Actions 예약은 지연·누락될 수 있습니다. 실제 응답을 받은 UTC 시각을 기록하며 과거 시각으로 소급하지 않습니다. 실패를 0명이나 직전 값으로 채우지 않습니다.
- 같은 실제 30분 관측 구간의 재실행은 중복 저장하지 않습니다. 0명은 API가 성공 응답으로 0을 반환했을 때만 저장합니다.
- 실패하면 마지막 정상 자료와 시각을 유지하고 상태를 `error`로 기록합니다. 부분 실패도 실행 결과는 실패로 표시하므로 Actions에서 확인할 수 있습니다.
- 수집 중 Git HEAD가 변경되면 강제 push하지 않습니다. 다음 실행에서 최신 설정을 기준으로 다시 생성합니다.

## 파일

| 경로 | 내용 |
|---|---|
| `data/opportunities.json` | 중복 제거 일정과 참여 조건·출처·연결 브리핑 |
| `data/topics.json` | 한국시간 오늘 포함 최근 7일 / 직전 7일 주제 집계 |
| `data/players.json` | 최근 30일 관측과 4주 요일×시간대 중앙값 |
| `data/status.json` | 마지막 수집 시도와 실패한 수집기 |
| `data/article-registry.json` | 원문 식별, 최초 브리핑 수록, 분류 근거, 제외 항목 |
| `data/calendar-supplements.json` | 기존 캘린더 형식의 보충 일정 호환용 내보내기. **기존 앱 일정표/알림에는 아직 연결하지 않음** |
| `observations/YYYY-MM.jsonl` | 실제 Steam 관측 이력. 삭제·초기화하지 마세요 |
| `state/issues/*.json` | 수록 근거 재현용 기존 브리핑 원본과 메타데이터 |
| `state/source-state.json` | 기존 feed Git tree·파일 SHA. 변경 파일만 재조회 |
| `config/event-supplements.json` | 공식 감시 HTML에만 있는 일정 등 운영자 확인 보충 자료 |
| `config/topics.json` | 공개 한·영 분류 사전과 규칙 버전 |
| `config/article-overrides.json` | 검증한 분류 수정, 번역·재인용 원문 묶음 |

## 집계 원칙

최근 화제는 **최근 브리핑에서 많이 다룬 주제**입니다. 전체 커뮤니티 여론이 아닙니다. 제목·요약·본문이 있는 기존 JSON에서만 분류하며 원문을 임의로 크롤링하거나 감정을 추정하지 않습니다.

현재 기존 JSON의 풍부한 `sections[].items[]` 형식과 간단한 `sections[]` 형식을 모두 읽습니다. 항목별 출처를 사용하며 전체 문서의 참고 링크 목록을 모두 기사로 세지 않습니다. 같은 원문 URL·Reddit 글 ID·YouTube 영상 ID는 한 건으로 합칩니다. 목록·홈·상품 페이지는 제외합니다. 같은 주제의 독립된 원문은 합치지 않습니다. 자동으로 확인할 수 없는 번역·재인용은 운영자가 `url_aliases`로 같은 정규 URL에 연결합니다.

기간은 원문 게시일 추정값이 아닌 **최초 브리핑 수록 시각의 KST 날짜**입니다. 재수록은 새 글로 세지 않습니다. 주제 비중은 주제별 독립 원문 수 / 기간의 전체 독립 원문 수이며 복수 분류로 합계가 100%를 넘을 수 있습니다. 이전 0건은 증가율 대신 새로 등장/해당 없음으로 표시합니다. 미분류와 기간별 표본 10건 미만을 표시합니다. 사전 분류는 운영자 검토가 필요합니다.

접속은 **Steam 전체 온라인 관측 인원**입니다. 한국시간은 표시 시간대일 뿐 한국·아시아 인원이나 콘솔 포함 통계가 아닙니다. 수집 시작 전 기록을 만들지 않습니다. 그래프의 최고는 관측 최고이며, 관측 사이 누락은 선을 끊습니다. 7일·30일 각각의 전체 기간 관측률과 수집 시작 이후 관측률을 구분합니다.

히트맵은 수집 시작 후 28일이 지나고 최근 28일의 같은 KST 요일·시간 칸에서 6개 이상, 서로 다른 날짜 3개 이상 관측한 경우만 중앙값을 표시합니다. 기준 미달 칸은 비워 둡니다. 이는 표시 품질 기준이며 통계적 대표성을 보장하지 않습니다.

## 접속량 판단과 지역별 서버 안내

앱은 당일을 제외한 최근 28일의 같은 KST 요일·시간대 관측 중앙값과 최신 인원을 비교합니다. 수집 시작 후 28일, 비교 관측 6회·서로 다른 3개 날짜를 충족해야 합니다. 중앙값 대비 120% 초과는 **평소보다 많음**, 80–120%는 **평소 수준**, 80% 미만은 **평소보다 적음**입니다. 분모가 0이거나 이력 부족, 수집 오류, 90분 지연이면 판단하지 않습니다. 접속 인원이 적다고 장애로, 많다고 정상·불안정으로 판정하지 않습니다.

현재 공식 Steam API는 전체 인원만 제공합니다. 아시아·US West 인원을 분할하거나 임의 비율로 추정하지 않습니다. 업데이트·점검 날짜가 현재와 겹치면 공식 공지 확인을 안내합니다.

지역별 실제 장애 안내는 `config/event-supplements.json`의 `server_notices`에서 운영자가 확인한 공식 발표만 관리할 수 있습니다. 현재 목록은 비어 있고, 앱은 **현재 상태 미확인**으로 표시합니다. 자료가 없다는 이유로 정상이라 표시하지 않습니다. 기존 GPT 공식 감시가 이 목록을 자동 채우지는 않습니다.

각 안내는 `id`, `region`(`global`/`asia`/`us-west`), `status`(`degraded`/`outage`/`maintenance`/`resolved`), `title`, `summary`, `source_url`, `verified_at`, `expires_at`, `evidence`를 갖습니다. `source_url`은 실제 공식 발표, `evidence`는 확인 내용입니다. 만료는 확인 후 최대 7일 이내로 정하고 사건의 변화를 확인하면 더 빨리 갱신하세요. 오래된 장애가 복구 안내 만료 후 다시 나타나지 않도록 지역별 최신 안내만 적용합니다. 날짜가 지났거나 자료 조회가 지연되면 현재 상태 미확인으로 돌아갑니다.

## 보충 일정 관리

`config/event-supplements.json`의 `calendar_events`를 편집합니다. 필수 필드:
`event_id`, `title`, `summary`, `start_date`, `end_date`, `source_url`, `status`, `verified_at`, `evidence`.
정확한 시간 확인 시 `start_at`/`end_at`에 UTC(`Z`) 또는 명시적 오프셋을 기록합니다. 시간대 없는 timestamp는 UTC로 해석합니다. 날짜만 확인되면 두 필드는 `null`로 둡니다. 날짜만으로 09:00 등의 알림 시각을 만들어 넣지 마세요.

- 같은 이벤트는 기존 feed의 `event_id`를 재사용합니다. 이후 정규 브리핑이 다른 ID를 사용하면 `event_aliases`에 `정규브리핑ID: 기존보충ID`로 연결합니다.
- 더 최신 정규 브리핑의 취소/수정은 오래된 보충 자료보다 우선합니다. 취소하려면 같은 ID와 새 `verified_at`, 취소 근거, `status: cancelled`를 남깁니다.
- 기간과 시작일 한 점을 혼동하지 않습니다. 종료 미확인 이벤트를 14일짜리 기간으로 늘리지 않습니다.
- `kind`: `play`(플레이), `watch`(시청), `subscription`(구독), `maintenance`(이용 제한), `update`, `release`, `other`.
- `participation`과 `platforms`는 확인한 조건만 작성합니다. 미확인 사항은 명시합니다.
- 시간대 자체가 미확인인 날짜는 `date_basis: announced_date_timezone_unknown`로 표시합니다. 해당 날짜를 KST 확정 시간처럼 보이지 않게 합니다.
- 공식 감시 HTML은 자동 파싱하지 않습니다. 운영자가 공식 출처와 대조해 이 파일을 갱신해야 합니다. 기존 GPT 예약은 이 파일을 자동 관리하지 않습니다.
- 현재 입력된 Twitch 일정은 2026-10-16 확인한 Twitch 공식 Stream-o-Ween 안내에 근거합니다. 시각·시간대·세부 조건은 미확인입니다.

## 기존 일정표 활용 검토

호환용 `calendar-supplements.json`은 기존 `BriefingCalendarEventData` 필드로 읽을 수 있습니다. **연결은 별도 승인 후 적용할 변경**입니다.
권장 연결 위치는 일정표 페이지 전용 병합 계층입니다. 기존 공유 `BriefingCalendarService`를 바로 바꾸면 알림 worker에도 영향이 갈 수 있습니다. 동향 보충 조회 실패 시 기존 일정만 유지, 같은 ID 중복 제거, 확인일 우선 수정/취소, 보충 출처 표시가 필요합니다.
시각 미확인 일정의 알림 여부와 통보 시각은 별도로 결정해야 하며, 이번 버전은 기존 푸시 스케줄러를 변경하지 않습니다.

## 검증과 운영

```sh
python3 -m unittest discover -s tests -v
python3 tools/collect.py
```

설정 변경 push와 Actions의 Run workflow로 즉시 실행할 수 있습니다. `data/status.json`, 마지막 관측 시각과 Actions 결과를 확인하세요. 마지막 관측 90분 초과는 앱에서 지연 상태입니다. 수집 중단은 이 저장소의 workflow만 비활성화하면 됩니다. 기존 feed와 GPT 예약은 계속 동작합니다. 60일간 저장소 활동이 없으면 GitHub가 예약을 비활성화할 수 있으므로 운영 상태를 확인하세요.

앱 호환 버전은 `schema_version: 1.0`입니다. 스키마가 맞지 않는 새 응답은 앱이 마지막 정상 캐시로 대체합니다. 원자적 Git 커밋으로 데이터 파일들을 함께 게시합니다. `config`·수집 코드를 수정할 때 기존 `observations`·`state`를 초기 자료로 덮어쓰지 마세요.

출처: [Steam 공식 API](https://partner.steamgames.com/doc/webapi/ISteamUserStats#GetNumberOfCurrentPlayers), [GitHub 예약 동작](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule), [공개 runner 비용 기준](https://docs.github.com/en/billing/concepts/product-billing/github-actions), [Twitch 공식 안내](https://blog.twitch.tv/en/2026/10/01/stream-o-ween/).
