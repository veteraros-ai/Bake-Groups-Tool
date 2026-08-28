# Bake Tools Blender 0.8.7

- Менеджер больше не запускается автоматически при открытии `.blend`; запуск выполняется явно из вкладки `Bake Tools`.
- Псевдопристыковка перенесена с внешней рамки Blender в область нативного `VIEW_3D Sidebar`.
- `Color HP` теперь реально окрашивает только HP-members по сабгруппам и восстанавливает исходные object colors.
- `Keep HP` импортирует непосредственные дочерние Empty/Collection-группы HP-root и их mesh descendants в реальный membership без изменения parenting/collections.
- Добавлены headless regression для Color/Keep/startup и GUI-проверка HWND owner/Sidebar geometry.
