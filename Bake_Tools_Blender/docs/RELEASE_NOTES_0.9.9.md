# Bake Tools Blender 0.9.9

## Maya-style Mesh Check

- Одиночное окно-отчёт заменено последовательным preflight-процессом оригинала.
- Дубликаты: `Select`, `Remove Extra Copies`, `Skip`.
- Возможные ZBrush-меши: `Select`, `Add to ZBrush Layer`, `Skip`.
- Меши с независимыми loose parts: `Select`, `Separate`, `Skip This Chapter`, `Skip`.
- `Select` останавливает проверку для ручного исправления; исправление или `Skip` продолжают следующую категорию.
- Удаление копий сохраняет один меш каждой группы и пропускает linked-объекты или объекты с дочерними элементами.

## Первый Analyze HP

- Для ещё не проверенной главы появляется Maya-подобное предложение `Check Now / Continue / Cancel`.
- `Check Now` запускает полный preflight и продолжает Analyze HP только после завершения цепочки.
- Статус проверки хранится только в текущей UI-сессии, как в Maya, и не загрязняет `.blend`.

## Локализация и тесты

- Новые окна и действия локализованы для EN, RU, JA и zh-CN.
- Добавлены проверки payload категорий, выбора, ZBrush-layer repair, удаления копий, разделения loose parts и состава первого Analyze HP dialog.
