# PNG 자동 누적 워터마크 실험

Git 저장소에는 코드·테스트·설치 안내를 올립니다. `results/`, `png_workspace/`, `.chainmark/`와 키·이벤트 DB는 `.gitignore`로 제외합니다. 아래의 실행 결과 파일은 로컬 제공본에 있으며, 저장소를 복제한 뒤에는 `png_demo.py`를 실행해 새로 생성할 수 있습니다.

현재 실험의 시작 파일은 **`png_demo.py`**입니다. PDF 변환 없이 PNG 픽셀에 DCT 패턴을 누적합니다. 별도 확장자나 웹 UI는 사용하지 않습니다.

## 1. PyCharm에서 바로 실행

프로젝트 폴더를 열고 Python 3.12 인터프리터를 설정한 뒤 아래 PNG 전용 의존성을 설치하세요.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-png.txt
.\.venv\Scripts\python.exe png_demo.py
```

PyCharm에서는 `png_demo.py`를 우클릭해 Run 해도 됩니다. 한 번 실행하면:

1. 새 실험 폴더와 합성 설계도 `source.png` 생성
2. **별도 감시 프로세스** 시작
3. 실제 파일 복사: `source.png` → `A/drawing.png`
4. 감시 프로그램이 A 패턴을 삽입하고 `READY OWNER -> A` 출력
5. 실제 파일 복사: `A/drawing.png` → `B/drawing.png` → B 패턴 자동 추가
6. 실제 파일 이동: `B/drawing.png` → `C/drawing.png` → C 패턴 자동 추가
7. 각 단계 검출 점수, 임계값, 원본 대비 PSNR/SSIM, 등록된 전달 순서 저장
8. 교체형 C-only baseline, 무워터마크 대조군과 디지털 왜곡 비교
9. 감시 프로세스 정상 종료

전체 강도 3단계 테스트는 `python png_demo.py --trials 3`입니다. `--out results/my_png_run`으로 결과 폴더를 지정할 수 있습니다. 이미 내용이 있는 폴더는 덮어쓰지 않습니다.

## 2. 직접 마우스로 PNG 복사·이동하기

**`png_watch.py`를 인자 없이 실행**하세요. 프로젝트 아래 `png_workspace`를 생성하고 계속 감시합니다. 같은 실험을 재시작하면 기존 설정과 등록 이력을 유지합니다.

탐색기에서 다음 순서로 작업하세요.

```text
png_workspace/
    source.png       ← 깨끗한 원본, 여기에 있는 파일은 감시하지 않음
    A/               ← 여기에 source.png를 복사
    B/               ← A의 처리가 끝난 PNG를 복사/이동
    C/               ← B의 처리가 끝난 PNG를 복사/이동
```

**매 단계 콘솔의 `READY`를 확인한 뒤 다음 폴더로 옮기세요.** 감시 중인 폴더 안의 PNG는 새 패턴이 추가된 파일로 교체됩니다. 원래 들어온 바이트와 처리 결과는 `.chainmark/objects/`에 보관합니다. 복사 중인 파일은 기본 0.4초 동안 내용이 안정된 뒤 처리합니다. PNG를 뷰어가 잠그고 있으면 닫은 뒤 기다리세요. 같은 준비 결과를 재시도하므로 실패할 때마다 패턴이 증폭되지 않습니다.

검출하려면 별도 실행 설정이나 명령줄에서:

```powershell
.\.venv\Scripts\python.exe png_watch.py inspect --workspace png_workspace --image png_workspace/C/source.png --output png_workspace/detection.json
```

위 파일명은 직접 복사한 파일명에 맞추세요. 감시 종료는 PyCharm 콘솔의 Ctrl+C 또는 아래 명령으로 요청할 수 있습니다. IDE의 강제 중지도 가능하며 다음 실행 때 OS 잠금이 해제되고 저장한 이력으로 이어집니다.

```powershell
.\.venv\Scripts\python.exe png_watch.py stop --workspace png_workspace
```

자신의 PNG로 새 실험을 하려면:

```powershell
.\.venv\Scripts\python.exe png_watch.py init --workspace results/custom_png --source C:/path/to/drawing.png
.\.venv\Scripts\python.exe png_watch.py watch --workspace results/custom_png
```

한 작업 공간에는 한 원본 문서와 A/B/C 폴더를 사용합니다. 새 문서는 새 작업 공간으로 등록하세요. 하위 폴더는 감시하지 않습니다. 대문자 `.PNG`도 처리하며, APNG·PNG로 이름만 바꾼 다른 형식은 지원하지 않습니다. 투명 PNG는 흰 배경의 RGB로 합성하고, 단일 이미지 최대 3천만 픽셀을 허용합니다.

## 3. 무엇을 증명하고 무엇을 모르는가

- **A/B/C는 폴더에 부여한 실험용 사용자 ID입니다.** 실제 사람의 인증·권한 제어·유출 차단 기능은 없습니다.
- 감시기는 폴더에 PNG가 **도착한 것**을 관측합니다. OS의 복사와 이동을 구별하지 않습니다. 발신자는 파일의 정확한 해시와 연결된 이전 등록 배포본의 ID입니다. 예를 들어 등록 A 배포본이 C에 들어오면 A→C 분기로 기록합니다. 최신 이벤트가 B라고 해서 B→C로 꾸미지 않습니다.
- 감시 프로그램을 거치지 않은 외부 메신저 재전송은 새 코드를 추가하지 않습니다. A의 파일이 외부 X를 거쳐 B 폴더에 들어와도 X는 알 수 없습니다. 등록된 A→B 연결이 실제 세계의 직접 전달을 입증하는 것은 아닙니다.
- 최종 PNG에서 A/B/C 패턴 검출은 픽셀 기반입니다. 순서는 별도의 HMAC 서명된 등록 이력으로 확인합니다. `.chainmark/events.sqlite3`의 부모 이벤트가 없거나 서명이 다르면 순서는 미확인입니다.
- PNG 메타데이터에는 영수증 ID만 담습니다. 검출기는 이 값을 보고 패턴이 있다고 판정하지 않습니다. 메타데이터를 제거한 PNG에서도 픽셀 패턴은 검출됩니다. 하지만 파일 바이트가 바뀌면 등록된 해시와 정확히 일치하지 않으므로 순서는 미확인으로 반환합니다. `metadata_stripped_detection.json`이 이 사례입니다.
- 같은 사용자에게 되돌아오면 새 패턴을 중복 삽입하지 않고 기존 픽셀을 유지한 새 영수증을 만듭니다. A→B→A는 A/B 코드와 등록된 반환 순서를 갖습니다. 동일 입력을 같은 폴더에 다시 복사하면 기존 영수증을 재사용하므로 **개별 복사 횟수는 셀 수 없습니다.**
- `.chainmark/`의 키·DB·원본을 같은 계정이 모두 수정할 수 있는 로컬 PoC입니다. 관리자 침해나 악의적인 동시 쓰기를 막는 보안 경계가 아닙니다. HMAC만으로 로그 전체 삭제나 키 탈취 후 재작성까지 막지 못합니다. 별도로 보관한 외부 감사 기록은 아직 없습니다.
- 원본 이미지와 키가 있어야 검출할 수 있습니다. 기본 상관 임계값 **0.12는 이 PNG 실험에서 보정하지 않은 기능 확인용 값**입니다. 전체 모집단의 오탐률을 보장하지 않습니다. 실제 카메라 촬영도 검증하지 않았습니다.

## 4. 주요 파일과 실제 실행 결과

- `chainmark/pngwatch.py`: 폴더 감시, 안정화 대기, SQLite 등록 그래프, 중복 방지, 원자적 교체·재시작 복구
- `chainmark/pngio.py`: 순수 PNG 입출력과 합성 설계도 생성
- `png_demo.py`: 별도 감시 프로세스와 실제 복사/이동을 사용하는 자동 시연
- `png_watch.py`: 수동 실험과 개별 검출 명령
- `tests/test_pngwatch.py`: 이동, 분기, 반복 전달, 파일 잠금, 재시작, 중복 삽입 방지, 출처 불명, 로그 손상 테스트

`results/png_auto_verified/`에는 이 컴퓨터에서 실제 실행한 결과가 있습니다.

```text
source.png                         원본 PNG
A/drawing.png                      A 패턴 배포본
C/drawing.png                      A+B+C 최종 배포본 (B 파일을 실제 이동)
snapshots/01_A.png,02_B.png,03_C.png 각 단계 보관본
step_scores.json                    단계별 점수·임계값·PSNR·SSIM·등록 경로
final_detection.json                최종 이미지의 검출과 등록 순서
events.json,observations.json       등록 이벤트와 미확인 관측
replacement_C_only.png             교체형 baseline
replacement_scores.json            baseline 검출 결과
distortion_scores.json/.csv         실제 왜곡별 사용자 패턴 질의
distortion_summary.json             검출률·오탐률과 평가 조건
failures.json                       미검출·오탐·정렬 실패
metadata_stripped_detection.json    픽셀 검출과 정확한 파일 귀속의 차이
watcher_status.json                 종료 상태
```

공격 후 PSNR/SSIM은 정렬된 유효 중첩 영역에서 계산합니다. 단계별 원본 대비 전체 이미지 품질과 직접 비교하지 마세요. 정렬 실패는 양성에서 FN으로 집계되며 음성에서는 미검출이므로 `unscorable_queries`와 `false_positive_rate_scorable`도 함께 확인합니다. 반복 trials는 고정 강도 설정이지 독립 무작위 표본이 아닙니다.

PNG 전용 테스트:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_pngwatch.py -q
```

기존 PDF 실험은 별도의 기존 ChainMark 프로젝트에 남아 있습니다. PNG 전용 프로젝트에는 PDF 처리가 필요하지 않습니다. 이번 실행 경로는 PDF 라이브러리를 불러오거나 PDF를 생성하지 않습니다.
