# RAG Lab Kit

[![tests](https://github.com/MumuKim0212/rag-labkit/actions/workflows/tests.yml/badge.svg)](https://github.com/MumuKim0212/rag-labkit/actions/workflows/tests.yml)

[English](../README.md) | 한국어

믿을 수 있는 RAG 실험을 돌리기 위한 작업 절차, 템플릿, 작은 도구 모음이다. RAG 시스템을 대신 만들어 주지는 않는다. 대신 실험이 믿을 만하게 유지되도록 규칙을 지켜 준다. 카드 하나에는 변경 하나만 담고, 개선이라고 말하기 전에 노이즈부터 재고, test 분할은 한 번만 보고, 모델 호출 비용은 빠짐없이 기록한다.

0.1.x 릴리스라서 명령과 파일 형식이 릴리스 사이에 바뀔 수 있다.

이 문서는 저장소 루트의 `README.md`를 옮긴 것이다. 내용이 어긋나면 영어 `README.md`가 기준이다.

## 무엇을 막아 주는가

RAG 실험은 보통 "B가 A보다 낫다"로 끝나는데, 이 말을 쉽게 하게 만드는 일이 세 가지 있다. A를 다시 돌리면 점수가 달라지고, 튜닝하는 동안 test 분할을 보게 되고, 검색 지표를 올린 변경이 답변에는 아무 영향이 없기도 하다. labkit은 이 셋 앞에 측정값이나 거부를 하나씩 둔다. 아래 출력은 모델도 API 키도 없이 도는 작은 모의 프로젝트인 [예제](../examples/README.md)에서 가져왔다. 출력은 번역하지 않았다.

**다시 돌린 실행은 같은 실행이 아니다.** 아무것도 바꾸지 않고 베이스라인을 두 번 돌렸더니 한 문항이 달라졌다.

```transcript
$ python -m labkit.eval.paired out/grades/dev_000_baseline.jsonl out/grades/dev_000_baseline_r2.jsonl --max-score 2
n=24  out/grades/dev_000_baseline.jsonl 0.562  out/grades/dev_000_baseline_r2.jsonl 0.521  diff -0.042  95% CI [-0.125, +0.000]
out/grades/dev_000_baseline_r2.jsonl better 0 / out/grades/dev_000_baseline.jsonl better 1 / same 23
```

이후의 비교는 이 값을 `--noise 1`로 넘기고, 한 문항 차이는 노이즈라고 보고한다.

**test 분할은 잠겨 있다.** 워크플로의 마지막 단계까지 그렇다.

```transcript
$ labkit run --config configs/baseline.yaml --split test
test_000_baseline: changed vs baseline = nothing
labkit: error: test: the test split is locked until the final workflow step; pass --final then
(exit 1)
```

**나아진 대리 지표가 나아진 답변은 아니다.** 답변 단계에 청크를 둘이 아니라 넷 넘기자 컨텍스트에 든 근거의 비율이 0.65에서 0.90으로 올랐다.

```transcript
$ labkit evaluate --experiment dev_002_more_k_retrieval
dev_002_more_k_retrieval: complete=True, graded 0 of 24 questions
  evid@1 0.6500, evid 0.9000
```

모의 모델은 첫 번째 청크를 인용하는데 첫 번째 청크는 그대로였다. 답변 실행 전의 점검이 그렇게 알려 주고, 카드에 게이트를 통과하지 못했다고 적히면 `labkit run`이 모델 호출 비용이 들었을 실행을 거부한다.

```transcript
upside - risk = 0 vs noise 1: skip the answer run: it cannot show a gain
```

독립된 답변 실행 두 번으로 채택된 카드까지 포함한 전체 과정은 [examples/](../examples/README.md)에 있고, `python examples/run_demo.py`로 30초쯤 걸려 다시 돌려 볼 수 있다.

## 설치

```bash
git clone https://github.com/MumuKim0212/rag-labkit.git
cd rag-labkit
pip install -e .            # Python 3.10 이상, PyYAML에 의존한다
pip install -e ".[env]"     # python-dotenv도 설치해 프로젝트의 .env를 읽는다
```

클론하지 않고 GitHub에서 바로 설치할 수도 있다. 프로젝트의 `requirements.txt`가 가리키는 곳이다(릴리스는 `labkit.__version__`과 같은 git 태그 `vX.Y.Z`이고, PyPI에는 올리지 않는다):

```bash
pip install "rag-labkit[env] @ git+https://github.com/MumuKim0212/rag-labkit.git@v0.1.9"
```

## 프로젝트 시작

```bash
labkit init my-rag --name "My RAG" --git     # 템플릿을 빈 폴더에 복사한다
cd my-rag
labkit status                                # 여덟 단계 중 현재 단계를 보여 준다
```

그다음 새 프로젝트의 `docs/WORKFLOW.md`와 `docs/steps/`의 현재 단계 파일을 따른다. 여덟 단계의 설명은 템플릿의 [WORKFLOW.md](../labkit/template/docs/WORKFLOW.md)에 있다. 자신의 코드(시스템, 판정자, 추가 지표)는 이름으로 등록하고 `labkit.yaml`의 `modules:`에 적는다.

## 명령

| | |
|---|---|
| `labkit run / grade / evaluate / compare / report` | 실험 루프. test 분할에는 `--final`이 필요하다 |
| `labkit show EXPERIMENT` | 실행이 기록한 답변, 컨텍스트, 인용, 채점을 읽는다. 스모크 행은 따로 표시한다 |
| `labkit golden-check` | 골든셋의 근거 인용문이 대상 페이지에 실제로 있는지 검사한다 |
| `labkit new-card SLUG` | 다음 실험 카드와 변형 설정 |
| `labkit status / check / tick / approve / revoke` | 여덟 단계의 진행을 다룬다. 충족된 기준과 승인한 사람을 보고, 기준을 체크하고, 단계를 승인하거나 취소한다(체크·승인·취소는 `main`에서만) |
| `labkit init PATH` | 템플릿에서 프로젝트 시작 |
| `/labkit-next` (Claude Code) | 에이전트가 현재 단계를 수행하고, 확인한 내용을 기록한 뒤, 사용자의 승인을 받으려고 멈춘다 |

옵션은 `labkit --help`와 `labkit <command> --help`가 보여 준다.

## 구성

```
labkit/             패키지: 평가 도구, 하네스, 단계, 카드, CLI
labkit/template/    `labkit init`이 복사하는 것: WORKFLOW + 단계별 파일, 카드, CLAUDE.md, 설정, report/
examples/           모의 시스템으로 따라 해 볼 수 있는 예제와 그 출력
tests/              python -m pytest
docs/               이 저장소 자체의 문서(한국어 README)
```

## 라이선스

[MIT License](../LICENSE)
