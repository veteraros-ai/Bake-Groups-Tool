# Bake Tools Blender 0.9.8

## Интерфейс и Blender integration

- Удалено случайно выведенное поле `gl.` из строк сабгрупп в Export Settings.
- Qt-менеджер синхронизирован с активной вкладкой Sidebar: вне `Bake Tools` он невидим и не перехватывает мышь, при возврате появляется автоматически.
- Сохранены доступные штатные области Blender: resize-граница Sidebar, вертикальная полоса вкладок и верхняя строка управления.
- Модальные диалоги Blender и in-client popover/menu всегда подавляют менеджер, поэтому Save confirmation, Options и другие окна не оказываются под ним.

## Debug / Support

- `Save Debug Log` сохраняет видимый журнал, историю действий, снимок текущих глав/сабгрупп/membership и Analyze/Assign debug.
- `Save Support Package` создаёт ZIP с текстовым отчётом, окружением Blender/Python/native core, списком коллекций и JSON-снимком сессии. Геометрия и `.blend` в пакет не копируются.
- Оба действия находятся в контекстном меню Log и используют неблокирующие owned dialogs.

## О программе и обновление

- Простое окно About заменено Maya-подобным окном Bake Groups Tool Update: иконка, автор, installed/latest/previous, контакт, Check, Show manual, Rollback, Release Notes и Close.
- Проверка обновлений выполняется в фоне и не блокирует Blender.
- Rollback использует локальный release ZIP и применяется только при следующем запуске Blender, до загрузки Python/native модулей аддона.
- Добавлено локализованное руководство для Blender.

## Проверено

- Blender 5.1 / Windows x64 / Python 3.13.
- Headless diagnostics/About, основной workflow smoke test.
- GUI pseudo-dock bounds, nonmodal owned dialogs, native transient priority и Sidebar-tab suppression.
