# Сравнение Analyze HP: Maya 1.4.3 и Blender 1.0.1/1.0.2

Повторный эталон: `Maya_Test_BakeGroups_Support_20260824_130733.zip`.
Сравниваемый запуск: `Blender_Test_BakeGroups_Support_20260824_130713.zip`.
Оба отчёта относятся к главе `Suspension_02`, содержат 600 HP и 461 LP меш,
одинаковый naming и действительно одинаковые настройки: Vertex Proximity,
Optimal, Collision 15%, Ignore Floaters включен, Adjacent Link выключен,
Link Vertex 8, Link Distance 0.1%, Color HP включен, Keep HP выключен.

## Фактический результат

| Показатель | Maya | Blender |
|---|---:|---:|
| HP на входе | 600 | 600 |
| LP на входе | 461 | 461 |
| Сабгруппы | 11 | 9 |
| Bolts | 400 в одной группе | 248 в трёх Bolts; ещё 152 размазаны по Large/Huge |
| ZBrush | 50 в трёх отдельных группах | все 50 смешаны с Large/Huge |
| Medium | 80 в `Medium_001` | 28 в Large, 52 в Huge |
| Large | 20 в трёх смысловых группах | все 20 в Huge |
| Huge | 50 в трёх смысловых группах | смешаны с Bolts/Medium/ZBrush |
| Compound linking | выключен | выключен |

Точная матрица старого Blender-результата:

- `Bolts_001/002/003`: 208 + 36 + 4 HP;
- `Large_001`: 68 HP (30 Bolts, 28 Medium, 10 ZBrush Huge);
- `Huge_001..005`: 284 HP с перемешанными Bolts, Medium, Large, Huge и ZBrush;
- ZBrush-сабгрупп с префиксом `ZBrush_` не было.

## Причины расхождения

1. Blender превращал общего LP-owner в жёсткий HP-компонент. Maya использует LP
   как контекст и вес при упаковке, но не объединяет безусловно все HP одного LP.
   Поэтому Blender создаёт пространственно большие компоненты и относит множество
   болтов к Huge.
2. Maya вычисляет robust size thresholds: медиану, верхнюю полку, повторяющиеся
   vertex-count signatures, исключает bbox-выбросы и анализирует shape metrics.
   Blender использует фиксированные коэффициенты относительно одной медианы.
3. Maya хранит ZBrush как отдельную семантическую ветку упаковки
   (`ZBrush_Huge`, `ZBrush_Large` и другие). Blender 1.0.1 не передавал признак
   BakeTools ZBrush layer в чистый AnalysisService.
4. В Blender support package коллекция `BakeTools_ZBrush_Layer` содержала только
   30 из 50 эталонных ZBrush HP: topology threshold не распознал остальные 20,
   хотя их общий naming явно содержит `ZBrush`.
5. Maya выполняет LP-family bolt reclassification; в эталонном запуске она
   вернула 20 Medium/Small items в Bolts. В Blender этого этапа нет.
6. Maya проверяет реальное пересечение мешей через C++ narrow phase после AABB.
   Blender при упаковке считает сам факт пересечения AABB коллизией, поэтому
   создаёт лишние buckets.
7. Maya после упаковки выполняет ownership repair и многоступенчатое финальное
   слияние до лимита. Blender лишь сообщает о превышении лимита и не выполняет
   эквивалентные strict/relaxed/macro/polish проходы.

## Коррекция Blender 1.0.2

1. В `MeshSnapshot` передаются `is_zbrush` и `semantic_group`, а в
   `AnalysisSettings` — физический scale Blender unit.
2. BakeTools collection/marker остаются основным источником ZBrush membership.
   Для Maya round-trip добавлен безопасный fallback по явному `ZBrush` naming;
   Find ZBrush и preflight checker используют тот же fallback.
3. Сохранённый в naming смысловой остров (`Bolts_001`, `Huge_003`,
   `ZBrush_Huge_002` и т. п.) обрабатывается как Maya hard GT/custom cluster.
   Это предотвращает повторное перемешивание уже размеченного chapter.
4. Безусловный hard union по LP-owner удалён. LP остаётся контекстом matching.
5. Для новых, не размеченных мешей перенесены Maya robust thresholds,
   repeated vertex-count bolt signatures, equivalent-volume categorization и
   C++ narrow-phase collision после AABB.
6. Bolts пакуются в один семантический bucket, как в Maya, а ZBrush проходит по
   отдельным `ZBrush_Huge/Large/Medium/Small` очередям.

Проверка `tools/support_distribution_compare.py` на двух новых support-пакетах
дала полное совпадение состава: 600/600 HP и те же 11 групп с теми же counts:

| Группа | Maya | Blender 1.0.2 |
|---|---:|---:|
| Bolts_001 | 400 | 400 |
| Huge_001 / 002 / 003 | 24 / 6 / 20 | 24 / 6 / 20 |
| Large_002 / 003 / 005 | 2 / 14 / 4 | 2 / 14 / 4 |
| Medium_001 | 80 | 80 |
| ZBrush_Huge_001 / 002 | 30 / 4 | 30 / 4 |
| ZBrush_Large_001 | 16 | 16 |

Важно: fixture доказывает корректное сохранение идентичной Maya-разметки при
round-trip. Для совершенно новых мешей без semantic naming используется
геометрический fallback; он стал существенно ближе к Maya, но ownership repair
и все четыре поздних strict/relaxed/macro/polish merge-прохода ещё не являются
буквальной построчной копией Maya worker.

Исправление duplicate checker остаётся отделено от Analyze HP: Maya использует допуск
0.001 сантиметра, который для стандартной метрической Blender-сцены равен
0.00001 Blender unit. Старый порт ошибочно применял 0.001 Blender unit.
