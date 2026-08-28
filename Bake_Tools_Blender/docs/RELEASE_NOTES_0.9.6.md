# Bake Tools Blender 0.9.6

- Диалоги подтверждения Blender, системные окна и popover-меню теперь располагаются выше Qt-менеджера.
- Z-order синхронизируется только для видимых same-process transient HWND и без активации, перемещения или изменения размеров окон.
- Для меню, рисуемых внутри главного HWND Blender, добавлен временный transparent/click-through fallback; Qt-окно и его event pump не закрываются.
- Сохранены ограничения безопасности: нет `WindowStaysOnTopHint`, модального Qt event loop, `SetParent/WS_CHILD` и повторных `SetWindowPos` при move/resize Blender.
- Подключены исходные словари Maya и отдельный Blender override-каталог.
- EN, RU, JA и zh-CN применяются к существующему дереву виджетов, динамическим строкам, основным runtime-сообщениям журнала, контекстным меню, tooltip, placeholder и новым диалогам.
- Добавлены smoke-тесты переключения языка и z-order Blender transient window.
