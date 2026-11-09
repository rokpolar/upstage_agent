# 기본 지출결의서 양식

- `지출결의서_기본양식.hwpx`: 계정과목·적요·지급금액·지급처·지급방법·증빙 첨부 표와 결의일·기안자 줄이 있는 기본 양식. 값 칸에 `{{account}}` 같은 자리표시가 있다.
- `provenance.json`: 양식 출처(원본 Word 해시, HWPX 생성에 쓴 [korean-report-hwpx](https://github.com/UpstageAI/korean-report-hwpx) 커밋, MIT).

채우기는 품의서와 같은 `tools/fill_form.py`가 `config/form_roles_gyeolui.json` 사전으로 한다. 자리표시 이름은 그 사전의 `fields` 역할 이름과 같다.
기관 양식을 쓰려면 화면의 "양식 업로드"로 HWPX를 올리면 된다(라벨로 칸을 찾으므로 자리표시는 없어도 된다).
