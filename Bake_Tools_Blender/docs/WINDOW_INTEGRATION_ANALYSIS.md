# Анализ блокировки окна Blender

## Симптом

При открытом менеджере граница Blender могла перекрываться отдельным Qt Tool window. Пользователь не мог нормально тянуть resize-border, перемещать Blender между мониторами и управлял закрытием через чужую системную шапку Qt.

## Причины в 0.8.7

1. `BakeToolsWindow` оставался самостоятельным top-level popup HWND с `WS_CAPTION`, `WS_THICKFRAME`, `WS_SYSMENU` и `WS_EX_TOOLWINDOW`. Native owner ограничивал topmost только Blender, но не превращал менеджер в часть его client area.
2. Менеджер выравнивался по правой границе Sidebar и мог физически перехватывать мышь над non-client resize-border Blender.
3. Dock timer каждые 180 мс возвращал Qt popup к рассчитанной screen geometry и сохранял большой minimum size.
4. `QDialog.exec()`, статические `QMessageBox/QInputDialog/QFileDialog` и `QMenu.exec()` запускались из Qt event pump, который сам вызывается Blender timer. Такой nested event loop останавливал нормальное выполнение Blender до закрытия окна/меню.
5. `QDialog.open()` в Qt 6 автоматически меняет modality на `WindowModal`, даже если перед ним был задан `NonModal`.

Диагностика до исправления: `owner_enabled=True`, `modality=0`, но стиль менеджера был `0x96cf0000`, extended style `0x180`. То есть Blender не был формально disabled; блокировка создавалась отдельной рамкой/перехватом ввода и вложенными event loops.

## Ошибочный эксперимент 0.8.8

Перевод Qt HWND в `WS_CHILD` через Win32 `SetParent` успешно выглядел в короткой автоматической проверке, но в реальной интерактивной сессии нарушил Qt window lifecycle: менеджер не отрисовывался, а Blender зависал и не завершался через обычный `WM_CLOSE`. Qt top-level widget нельзя безопасно превращать в child чужого non-Qt HWND после создания. Этот путь полностью удалён в 0.8.9.

## Безопасное решение 0.9.2

- Qt QWidget остаётся top-level Qt window; native `SetParent` и `WS_CHILD` не используются.
- Через `GWLP_HWNDPARENT` назначается только Blender owner, поэтому окно скрывается/закрывается вместе с Blender без вмешательства в Qt dispatcher.
- Caption/thick-frame/system-menu отсутствуют благодаря `FramelessWindowHint`; Sidebar screen rectangle применяется обычным `SetWindowPos(..., SWP_NOACTIVATE)`.
- Менеджер стал `FramelessWindowHint + NonModal + WA_ShowWithoutActivating` и больше не вызывает `activateWindow()`.
- Все диалоги используют `NonModal + show()`. Контекстные меню используют `popup()`. В рабочем UI не осталось `exec()`.
- Закрытие/повторное открытие выполняется Blender-операторами из вкладки `Bake Tools`; скрытие останавливает Qt pump и dock timer.
- Overlay располагается только внутри содержимого client Sidebar: ниже строки Close, левее вертикальной полосы вкладок и на 8 px правее левой границы региона. Поэтому он не перекрывает viewport navigation controls, а нативная граница изменения ширины Sidebar остаётся доступной.
- Qt minimum width снижен; жёсткие minimum width колонок удалены. От 470 px показывается исходная двухколоночная компоновка, ниже — одна колонка с переключением `Main` / `Matcher / TOC`.
- Повторяющийся `QTimer` позиционирования удалён: dock sync выполняется из Blender timer и только когда screen rectangle реально изменился.
- Через `GetGUIThreadInfo` отслеживается `GUI_INMOVESIZE`; во время системного move/resize не вызываются ни Qt `processEvents()`, ни Blender context bridge, ни `SetWindowPos`.
- `SetWindowPos` использует `SWP_NOOWNERZORDER | SWP_NOSENDCHANGING`, чтобы не создавать дополнительный owner/z-order цикл.

## Проверка

- Native style после исправления: frameless popup `0x96000000`, extended tool style `0x80`, `embedded=False`, owner установлен.
- Blender owner остаётся enabled и программно проходит move+resize при видимом менеджере.
- Native manager rectangle меняется вместе с parent и остаётся внутри Blender frame.
- Левая/правая границы manager проверяются относительно `VIEW_3D/UI`; ширина равна ширине Sidebar за вычетом resize-lane и tab strip.
- GUI timer завершается при открытом тестовом QMessageBox, подтверждая отсутствие modal/nested-loop блокировки.
- Hide/Show из Blender Sidebar сохраняет один экземпляр UI и повторно восстанавливает safe owner relationship.
- Отдельный тест отправляет реальный Win32 `WM_CLOSE` главному окну при видимом менеджере; Blender штатно завершается.
- GUI diagnostics перемещает и изменяет размер owner, проверяет защиту move/resize и подтверждает однократный dock sync после завершения операции.

Ограничение: это frameless owned overlay, а не Qt-виджет внутри RNA-layout. Поэтому для отображения менеджера область `VIEW_3D Sidebar` должна быть открыта; данные и операторы при этом остаются полностью Blender-native.

## Рефакторинг 1.1.9: точечный BQt-подобный window manager

- Добавлен `qt_window_manager.py`: единый реестр Qt-окон по `objectName`, повторное использование `BakeToolsBlenderWindow`, weak references для диалогов и один владелец Blender timer.
- Все suppression reasons и базовый Win32 exstyle хранятся в одной записи окна. Совместимые `_bt_*` атрибуты остаются только для диагностики и старых smoke-тестов.
- `blender_bridge.py` больше не реализует собственный набор suppression-state transitions; он обнаруживает Blender popup/transient и передаёт причину менеджеру.
- BQt full-wrap намеренно не перенесён: Blender остаётся исходным GHOST top-level, а Qt-окно остаётся безопасным owned frameless popup через `GWLP_HWNDPARENT`.
- `SetParent`, `WS_CHILD`, `WindowStaysOnTopHint`, Qt nested event loop и автоматическое открытие при загрузке сцены по-прежнему запрещены.

## Workspace visibility в 1.2.0

- При явном открытии менеджер запоминает RNA pointer текущего Blender Workspace, а не строку `Layout`. Поэтому переименование или локализация Workspace не ломает правило видимости.
- Причина `workspace` добавлена в общий набор suppression reasons: Qt HWND становится прозрачным и click-through, но Blender timer остаётся активным.
- При переходе в Modeling, Sculpting и любой другой Workspace менеджер скрывается. При возврате в исходный Workspace сначала пересчитываются Sidebar tab и screen geometry, затем HWND восстанавливается — без одного кадра в старой позиции.
- Явное открытие из другого Workspace начинает новую сессию привязки к нему. Смена Workspace сама по себе менеджер не запускает.
- Реализация не использует `bpy.msgbus`, обработчики depsgraph или Object selection и поэтому работает в пустой сцене.

## Немедленное восстановление после popup в публичной 1.0.0

Blender закрывает TEMPORARY popup нажатием MMB и этим же событием запускает modal viewport orbit. Предыдущий guard отслеживал только ЛКМ/ПКМ: native HWND менеджера оставался с alpha `0`, а `bpy.app.timers` не гарантировал ближайший тик во время drag. Поэтому окно возвращалось только после позднего 30-секундного expiry (на практике задержка воспринималась примерно как 15 секунд).

Теперь оба уровня guard учитывают MMB. `WH_GETMESSAGE` ставит состояние закрытия на `WM_MBUTTONDOWN`; первое `WM_MOUSEMOVE + MK_MBUTTON` означает, что закрывающее событие popup уже dispatch-нуто и начался orbit. В этот момент callback выполняет только обратные Win32-операции над уже известным manager HWND: возвращает alpha/exstyle и инвалидирует DWM surface. Qt, Blender API, `SetWindowPos` и обработка layout из callback не вызываются. Флаг `native_restore_pending` затем согласуется `QtWindowManager` на обычном pump; если остаётся другая причина (`workspace`, Sidebar tab или Blender modal dialog), она снова корректно подавляет окно.
