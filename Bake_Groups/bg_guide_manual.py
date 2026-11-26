# -*- coding: utf-8 -*-
"""Bundled visual Maya guide. Pur_0 screenshots are layout references only."""
from __future__ import absolute_import, division, print_function

import os
import struct
from functools import lru_cache
from bg_guide_translations import CAPTIONS, CARDS, RELEASE, SECTIONS as TRANSLATED_SECTIONS

try:
    import bg_version
    CURRENT_VERSION = bg_version.__version__
except ImportError:
    CURRENT_VERSION = ""

RELEASE_VERSION_RANGE = "1.5.0"
HELP_CARD_REVISION = 1


def release_title(language):
    translation = RELEASE.get(language)
    if translation:
        return translation["title"].format(RELEASE_VERSION_RANGE)
    if language == "ru":
        return "Что нового в версии " + RELEASE_VERSION_RANGE
    return "New in version " + RELEASE_VERSION_RANGE

try:
    from PySide6 import QtGui
except ImportError:
    from PySide2 import QtGui


@lru_cache(maxsize=4)
def guide_font_family(language):
    """Load a Windows CJK font when Maya's offscreen Qt has no font database."""
    if language == "ja":
        family, filename = "Yu Gothic", "YuGothR.ttc"
    elif language == "zh-CN":
        family, filename = "Microsoft YaHei", "msyh.ttc"
    else:
        return "Arial"
    if family in QtGui.QFontDatabase().families():
        return family
    path = os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "Fonts", filename)
    if os.path.isfile(path):
        font_id = QtGui.QFontDatabase.addApplicationFont(path)
        if font_id >= 0:
            families = QtGui.QFontDatabase.applicationFontFamilies(font_id)
            if family in families:
                return family
            if families:
                return families[0]
    return family


def _figure(image, en_caption="", ru_caption=""):
    return image, en_caption, ru_caption


def _card(key, en, ru, en_body, ru_body, images):
    if isinstance(images, str):
        images = ((_figure(images),),)
    return key, en, ru, en_body, ru_body, images


SECTIONS = (
    ("01", "Getting started", "С чего начать",
     "The working order: choose roots, create chapters, analyze HP, assign LP, then export.",
     "Порядок работы: выбрать корневые группы, создать главы, проанализировать HP, назначить LP и экспортировать.",
     (
         _card("overview", "The Bake Groups window", "Окно Bake Groups",
               "Use ? Help on any control to jump to its explanation. Double-click an image to enlarge it.",
               "Нажмите ? Help, затем элемент интерфейса — доска перейдёт к пояснению. Дважды щёлкните по изображению, чтобы увеличить его.",
               ((_figure("Untitled.png", "Choose the HP and LP roots", "Выбор корней HP и LP"),),
                (_figure("maya_window.png", "Full window — enlarge with a double-click", "Окно целиком — увеличьте двойным щелчком"),))),
         _card("pick.hp", "Choose the HP root", "Выберите корень HP",
               "Select the high-poly group in Maya and click Pick HP. Keep the hierarchy intact until the chapter is created.",
               "Выделите группу high-poly в Maya и нажмите Pick HP. Сохраняйте иерархию до создания главы.", "focus_hp.png"),
         _card("pick.lp", "Choose the LP root", "Выберите корень LP",
               "Select the low-poly group and click Pick LP. LP meshes are the targets for matching and baking.",
               "Выделите группу low-poly и нажмите Pick LP. LP-меши — цели сопоставления и запекания.", "focus_lp.png"),
         _card("chapter.create", "Create chapters", "Создайте главы",
               "Create builds the chapter. For several LP materials choose one chapter (split later during Analyze HP) or several chapters immediately. Keep HP preserves existing HP grouping when adding new roots.",
               "Create создаёт главу. При нескольких материалах LP выберите одну главу (разделение позже при Analyze HP) или сразу несколько. Keep HP сохраняет распределение HP при добавлении новых корней.",
               ((_figure("focus_create_chapter.png", "Pick roots, then Create", "Выберите корни, затем Create"),),
                (_figure("Untitled(1).png", "Choice for multiple LP materials", "Выбор при нескольких материалах LP"),),
                (_figure("Untitled(2).png", "One chapter", "Одна глава"),
                 _figure("Untitled(3).png", "Several chapters in a book", "Несколько глав в книге")),
                (_figure("focus_keep.png", "Keep HP when adding more roots", "Keep HP при добавлении новых корней"),))),
         _card("toc", "Books and chapters", "Книги и главы",
               "The Table of Contents shows books and chapters. Select a chapter to work with it; double-click a book name to rename it. The eye toggles visibility.",
               "Оглавление показывает книги и главы. Выберите главу для работы; двойным щелчком можно переименовать книгу. Глаз переключает видимость.",
               ((_figure("focus_chapter_tree.png", "Book with several chapters", "Книга с несколькими главами"),),
                (_figure("Untitled(41).png", "Edit a book name", "Переименование книги"),))),
         _card("toc.menu", "Chapter context menu", "Меню главы",
               "Right-click a chapter to select or move meshes, change export participation, rename the chapter, find matching meshes, manage its book, or delete the chapter. Split by materials appears when the chapter supports it. In Help mode, choose a command to open its explanation.",
               "ПКМ по главе открывает команды выделения и переноса мешей, включения в экспорт, переименования, поиска пары, работы с книгами и удаления главы. Для подготовленных глав доступно Split by materials. В режиме Help выбор команды открывает её описание.",
               ((_figure("Untitled(42).png", "Chapter context menu", "Контекстное меню главы"),),
                (_figure("Untitled(34).png", "Select meshes", "Выделить меши"),
                 _figure("Untitled(35).png", "Move selected meshes", "Перенести выделенные меши")),
                (_figure("Untitled(38).png", "Create a book", "Создать книгу"),
                 _figure("focus_extract_chapter.png", "Extract from book", "Извлечь из книги")))),
         _card("toc.meshes", "Select and move meshes", "Выделение и перенос мешей",
               "Select Meshes selects the meshes of the selected chapters or books in Maya. Move Mesh to transfers the currently selected scene meshes into the clicked chapter. Review the new HP and LP grouping afterward.",
               "Select Meshes выделяет в Maya меши выбранных глав или книг. Move Mesh to переносит выделенные в сцене меши в главу, по которой открыто меню. После переноса проверьте распределение HP и LP.",
               ((_figure("Untitled(34).png", "Select meshes", "Выделить меши"),
                 _figure("Untitled(35).png", "Move selected meshes", "Перенести выделенные меши")),)),
         _card("toc.export", "Include or exclude a chapter from export", "Участие главы в экспорте",
               "Exclude from export skips the chapter during export. The chapter stays in the Table of Contents. Use Include in export to restore its participation.",
               "Exclude from export пропускает главу при экспорте. Она остаётся в оглавлении. Include in export возвращает главу в экспорт.", ()),
         _card("toc.edit", "Rename or delete a chapter", "Переименование и удаление главы",
               "Rename Chapter changes the chapter name. Delete Chapter removes the chapter from the Table of Contents and leaves its meshes in Maya.",
               "Rename Chapter меняет имя главы. Delete Chapter удаляет запись из оглавления и оставляет меши в сцене Maya.", ()),
         _card("toc.book", "Move chapters between books", "Распределение глав по книгам",
               "Group into Book creates a book from the selected chapters. Add to opens a list of existing books. Pick a book to move the selected chapters there. Extract from Book returns the selection to the top level.",
               "Group into Book создаёт книгу из выбранных глав. Add to открывает список существующих книг. Выберите книгу, чтобы перенести туда главы. Extract from Book возвращает их на верхний уровень.",
               ((_figure("Untitled(38).png", "Create a book", "Создать книгу"),
                 _figure("focus_extract_chapter.png", "Extract from book", "Извлечь из книги")),)),
         _card("toc.split", "Split a chapter by material", "Разделение главы по материалам",
               "Split by materials creates chapters from an existing chapter with material subgroups such as M01 and M02. The command is available when LP meshes can be assigned to those chapters individually.",
               "Split by materials создаёт главы из существующей главы с подгруппами материалов, например M01 и M02. Команда доступна, когда LP-меши можно разнести по этим главам по отдельности.", ()),
         _card("toc.find_lost", "Find Lost (beta)", "Find Lost (beta)",
               "Select an HP or LP mesh first. Find Lost searches for matching opposite meshes and can move found meshes into the chapter. Because it changes grouping, inspect the result afterward.",
               "Сначала выделите HP- или LP-меш. Find Lost ищет подходящие меши противоположного типа и может перенести найденное в главу. Поскольку группировка меняется, затем проверьте результат.",
               ((_figure("Untitled(37).png", "Chapter menu command", "Команда в меню главы"),),
                (_figure("focus_find_lost.png", "Where to find it", "Где находится команда"),))),
     )),
    ("02", "Prepare and analyze HP", "Подготовка и анализ HP",
     "Check geometry and transforms first, then run Analyze HP and inspect the distribution.",
     "Сначала проверьте геометрию и трансформации, затем запустите Analyze HP и проверьте распределение.",
     (
         _card("hp.prepare.tools", "Combine and Separate", "Combine и Separate",
               "Combine joins selected meshes. Separate splits disconnected geometry into independent parts. Run Mesh Check after editing the geometry.",
               "Combine объединяет выделенные меши. Separate разделяет несвязанную геометрию на самостоятельные части. После правок запустите Mesh Check.",
               ((_figure("focus_combine_separate.png", "Preparation tools", "Инструменты подготовки"),),
                (_figure("focus_mesh_check_button.png", "Run Mesh Check", "Запуск Mesh Check"),),)),
         _card("hp.prepare.combine", "Combine meshes", "Объединить меши",
               "Combine joins selected mesh transforms into one mesh. Use it only when those parts should be treated as one HP object; inspect the result before Analyze HP.",
               "Combine объединяет выделенные меши в один объект. Используйте его, только если эти части должны обрабатываться как единый HP-меш; затем проверьте результат до Analyze HP.",
               ((_figure("focus_combine.png", "Combine is the first preparation button", "Combine — первая кнопка подготовки"),),)),
         _card("hp.prepare.separate", "Separate mesh shells", "Разделить меш на части",
               "Separate splits a selected combined mesh into independent mesh parts when possible. The combined-mesh warning can point to candidates; inspect the split result and re-run checks.",
               "Separate разделяет выделенный объединённый меш на самостоятельные части, если это возможно. Предупреждение об объединённых мешах поможет найти кандидатов; проверьте части и повторите проверку.",
               ((_figure("focus_separate.png", "Separate is the second preparation button", "Separate — вторая кнопка подготовки"),),
                (_figure("focus_disconnected.png", "Disconnected geometry check", "Проверка несвязанной геометрии"),))),
         _card("hp.check", "Mesh Check before analysis", "Проверка мешей перед анализом",
               "Open Mesh Check to review transforms, ZBrush candidates, duplicates, disconnected geometry and empty objects. Mesh Check uses the active chapter. Double-click a result to select its objects. The tab dot is green when the check is clear and red when problems are found.",
               "Откройте Mesh Check, чтобы проверить трансформации, кандидатов ZBrush, дубликаты, несвязанную геометрию и пустые объекты. Mesh Check проверяет активную главу. Дважды щёлкните по результату, чтобы выделить объекты. Зелёный кружок означает, что проблем нет, красный — что найдены проблемы.",
               ((_figure("focus_check_results.png", "Mesh Check results", "Результаты проверки мешей"),),)),
         _card("hp.check.duplicates", "Duplicate meshes", "Дубликаты мешей",
               "Inspect duplicate candidates. Remove only redundant copies after verifying which mesh belongs in the scene.",
               "Проверьте найденные дубликаты. Удаляйте лишние копии только после проверки того, какой меш нужен в сцене.", "focus_duplicates.png"),
         _card("hp.check.transforms", "Freeze Transformations", "Freeze Transformations",
               "The root groups and meshes must have valid transforms. If prompted, inspect the listed nodes before freezing them; re-run the check afterward.",
               "У корневых групп и мешей должны быть допустимые трансформации. Перед Freeze проверьте перечисленные узлы и затем повторите проверку.", "focus_transforms.png"),
         _card("hp.zbrush", "ZBrush candidates", "Кандидаты ZBrush",
               "Set the triangle percentage in Mesh Check and run the check. Review the ZBrush candidates and use the available fix to add them to the ZBrush layer.",
               "Задайте процент треугольников во вкладке Mesh Check и запустите проверку. Просмотрите кандидатов ZBrush и добавьте нужные меши в ZBrush-слой через действие исправления.",
               ((_figure("focus_zbrush_candidates.png", "ZBrush candidates", "Кандидаты ZBrush"),),
                (_figure("focus_zbrush_threshold.png", "ZBrush threshold", "Порог ZBrush"),),)),
         _card("hp.color", "Color HP by subgroup", "Окраска HP по сабгруппам",
               "Enable Color HP to inspect subgroup colors in the viewport. Right-click empty space in Groups and open Color HP palette. Edit colors next to the subgroup names, name a preset if needed, and choose whether to use it on the next launch. Cancel restores the previous colors.",
               "Включите Color HP для просмотра цветов сабгрупп во вьюпорте. Щёлкните правой кнопкой по пустому месту во вкладке Groups и откройте «Палитра Color HP». Меняйте цвета рядом с названиями сабгрупп, при необходимости сохраните именной пресет и назначьте его по умолчанию. Cancel возвращает прежние цвета.",
               ((_figure("focus_color_hp.png", "Groups tab", "Вкладка Groups"),),
                (_figure("focus_palette_command.png", "Groups context menu", "Контекстное меню Groups"),),
                (_figure("focus_palette_colors.png", "Colors next to subgroup names", "Цвета рядом с названиями сабгрупп"),),
                (_figure("focus_palette_presets.png", "Preset name and default", "Имя пресета и использование по умолчанию"),),)),
         _card("hp.analyze", "Analyze HP", "Анализ HP",
               "Run Analyze HP after preparing the scene. The tool distributes HP meshes into subgroups; visually check the result and correct any misplaced meshes before export.",
               "После подготовки сцены нажмите Analyze HP. Плагин распределит HP по сабгруппам; проверьте результат и исправьте ошибочно распределённые меши до экспорта.",
               ((_figure("focus_analyze.png", "Start analysis", "Запустить анализ"),),
                (_figure("focus_analysis_result.png", "Resulting subgroup list", "Полученный список сабгрупп"),))),
         _card("hp.algorithm", "Algorithm settings", "Настройки алгоритма",
               "Algorithm expands the HP analysis settings; opening it does not run analysis. It controls how ambiguous HP meshes are compared, matching tolerance, floater handling and optional adjacent-vertex linking. Start with defaults; changes take effect at the next Analyze HP.",
               "Algorithm раскрывает настройки анализа HP; само раскрытие анализ не запускает. Здесь выбираются способ сравнения неоднозначных HP-мешей, допуск сопоставления, обработка флоатеров и необязательное связывание соседних вершин. Начните с настроек по умолчанию; изменения применятся при следующем Analyze HP.",
               ((_figure("Untitled(75).png", "Full algorithm panel", "Панель алгоритма"),),
                (_figure("Untitled(76).png", "Collision", "Collision"),
                 _figure("Untitled(77).png", "Link Vertex", "Link Vertex")),
                (_figure("Untitled(78).png", "Link Dist", "Link Dist"),))),
         _card("hp.algorithm.strategy", "HP clustering strategy", "Стратегия группировки HP",
               "Vertex Proximity is the default and resolves ambiguous matches by geometry. Spatial Volume Match favors spatial position and bounding volume. Topology Fingerprint compares vertex and edge counts. If a scene groups poorly, try another strategy and re-run Analyze HP.",
               "По умолчанию Vertex Proximity разрешает неоднозначности по геометрии. Spatial Volume Match учитывает положение и объём bounding box. Topology Fingerprint сравнивает число вершин и рёбер. Если группировка неточна, смените стратегию и повторите Analyze HP.",
               ((_figure("focus_hp_strategy.png", "Strategy selector in the Algorithm panel", "Выбор стратегии в панели Algorithm"),),)),
         _card("hp.algorithm.optimization", "Analysis optimization", "Оптимизация анализа",
               "Optimal retains more geometric detail for matching; Speed uses a tighter sample cap on very dense meshes to reduce analysis time. The option does not change source geometry or exported mesh smoothing.",
               "Optimal сохраняет больше геометрических данных для сопоставления; Speed сильнее ограничивает выборку очень плотных мешей ради скорости анализа. Параметр не меняет исходную геометрию и сглаживание при экспорте.",
               ((_figure("focus_optimization.png", "Optimization selector", "Выбор режима оптимизации"),),)),
         _card("hp.algorithm.collision", "Collision tolerance", "Допуск Collision",
               "Collision (%) changes the geometric matching tolerance. Lower values are stricter and may split more HP into separate groups; higher values are looser and may merge unrelated parts. Change gradually and inspect the result.",
               "Collision (%) меняет допуск геометрического сопоставления. Меньшие значения строже и могут разделить HP на большее число групп; большие значения мягче и могут объединить лишнее. Меняйте постепенно и проверяйте результат.",
               ((_figure("Untitled(76).png", "Collision control", "Параметр Collision"),),)),
         _card("hp.algorithm.floaters", "Ignore Floaters", "Ignore Floaters",
               "When Ignore Floaters is checked, the automatic floater-detection pass is skipped. Uncheck it only when that pass is needed, then verify that bolts with their own LP are not assigned as floaters.",
               "При включённом Ignore Floaters автоматический этап распознавания флоатеров пропускается. Выключайте флажок только когда этот этап нужен, а затем проверьте, что болты с собственной LP не стали флоатерами.",
               ((_figure("focus_floaters.png", "Ignore Floaters in Algorithm", "Ignore Floaters в панели Algorithm"),),)),
         _card("hp.algorithm.link", "Adjacent vertex link", "Связь соседних вершин",
               "Enable the checkbox to join HP parts that have enough nearby vertices. Link Vertex sets the minimum number of matching vertices; Link Dist (%) is the proximity threshold relative to mesh size. Leave it off unless the scene needs compound-mesh linking.",
               "Включите флажок, чтобы связывать части HP с достаточным числом близких вершин. Link Vertex задаёт минимум совпадений, Link Dist (%) — расстояние относительно размера мешей. Если связь составных мешей не нужна, оставьте флажок выключенным.",
               ((_figure("Untitled(77).png", "Minimum matching vertices", "Минимум близких вершин"),
                 _figure("Untitled(78).png", "Maximum relative distance", "Предельное относительное расстояние")),)),
     )),
    ("03", "Work with subgroups", "Работа с сабгруппами",
     "Correct and inspect the result directly in the subgroup list.",
     "Проверяйте и при необходимости корректируйте результат прямо в списке сабгрупп.",
     (
         _card("hp.visibility", "Show and hide HP", "Показать или скрыть HP",
               "HP Visible toggles high-poly visibility for the current chapter. Use it to compare the distribution with LP.",
               "HP Visible переключает видимость high-poly в текущей главе. Используйте это для сравнения распределения с LP.",
               ((_figure("Untitled(12).png", "HP shown", "HP показан"),
                 _figure("Untitled(13).png", "HP hidden", "HP скрыт")),)),
         _card("lp.visibility", "Show and hide LP", "Показать или скрыть LP",
               "LP Visible toggles low-poly visibility. Visibility does not remove any geometry from the scene.",
               "LP Visible переключает видимость low-poly. Это не удаляет геометрию из сцены.",
               ((_figure("Untitled(14).png", "LP shown", "LP показан"),
                 _figure("Untitled(15).png", "LP hidden", "LP скрыт")),)),
         _card("groups.tab", "Groups tab", "Вкладка Groups",
               "Open Groups to see the subgroups in the active chapter. Use the row controls to show, move, lock or remove a group. Right-click a row for its commands, or use the list menu to search for a mesh or open the Color HP palette.",
               "Откройте Groups, чтобы увидеть сабгруппы активной главы. Кнопки строки управляют видимостью, переносом, блокировкой и удалением. ПКМ по строке открывает её команды, а меню списка позволяет найти меш или открыть палитру Color HP.",
               ((_figure("focus_groups_tab.png", "Groups tab", "Вкладка Groups"),),
                (_figure("focus_subgroup_controls.png", "Subgroup controls", "Управление сабгруппой"),))),
         _card("groups.visibility", "Subgroup visibility", "Видимость сабгрупп",
               "Groups Vis shows all, hides all or indicates a mixed state. Right-click it to isolate one material group. The eye on a row changes only that subgroup.",
               "Groups Vis показывает всё, скрывает всё или сообщает о смешанной видимости. Правым щелчком можно изолировать одну группу материалов. Глаз в строке меняет только одну сабгруппу.",
               ((_figure("Untitled(16).png", "All shown", "Все видимы"),
                 _figure("Untitled(17).png", "All hidden", "Все скрыты")),
                (_figure("Untitled(18).png", "Mixed visibility", "Часть скрыта"),
                 _figure("Untitled(19).png", "Material isolation menu", "Меню изоляции материалов")))),
         _card("groups.create", "Create a subgroup", "Создать сабгруппу",
               "Enter a name, then click Create Group. Selected meshes can be moved to the new subgroup with the plus button on its row.",
               "Введите имя и нажмите Create Group. Выделенные меши можно перенести в новую сабгруппу кнопкой «+» в её строке.",
               ((_figure("focus_group_create.png", "Enter name and create", "Имя и создание"),),
                (_figure("focus_new_group_row.png", "New subgroup row", "Строка новой сабгруппы"),))),
         _card("groups.row", "Subgroup controls", "Управление сабгруппой",
               "The row contains visibility, name, Move selected meshes, Lock and Delete. Double-click the name to select its meshes; right-click for Rename and Select Color. Lock protects a manual group during later HP analysis. Delete moves its meshes to the chapter root, not out of Maya.",
               "В строке есть видимость, имя, перенос выделенных мешей, замок и удаление. Двойной щелчок по имени выделяет меши; правый открывает Rename и Select Color. Замок защищает ручную группировку при повторном анализе HP. Удаление переносит меши в корень главы, не удаляя их из Maya.",
               ((_figure("focus_subgroup_controls.png", "Subgroup rows", "Строки сабгрупп"),),
                (_figure("guide_subgroup_menu.png", "Subgroup context menu", "Контекстное меню сабгруппы"),))),
         _card("hp.find_similar", "Find similar meshes", "Поиск похожих мешей",
               "Find Sim searches for matching HP meshes as a combined whole. Right-click the button for Find All: it searches for each of several selected meshes separately. Compare results with LP and subgroups before moving bolts.",
               "Find Sim ищет похожие HP-меши, рассматривая выделенное как единое целое. Правый щелчок открывает Find All: он ищет каждый из нескольких выделенных мешей отдельно. Перед переносом болтов сравните результат с LP и сабгруппами.",
               ((_figure("Untitled(31).png", "Find Sim — combined selection", "Find Sim — выделение как единое целое"),
                 _figure("Untitled(32).png", "Find All — separate selected meshes", "Find All — отдельный поиск каждого меша")),)),
         _card("groups.search", "Find a mesh or clean empty groups", "Поиск меша и пустые группы",
               "Right-click an empty area of the subgroup list. Find group by mesh locates the subgroup containing the selected Maya mesh; Delete empty groups removes subgroup entries without meshes.",
               "Щёлкните правой кнопкой по пустому месту списка. Find group by mesh найдёт сабгруппу выделенного в Maya меша; Delete empty groups уберёт пустые записи сабгрупп.",
               ((_figure("focus_groups_search.png", "Groups context menu", "Контекстное меню Groups"),),)),
     )),
    ("04", "Assign LP", "Назначение LP",
     "After HP grouping, associate LP with the subgroups and inspect the result.",
     "После группировки HP сопоставьте LP с сабгруппами и проверьте результат.",
     (
         _card("lp.assign", "Assign LP meshes", "Назначить LP-меши",
               "Click Assign LP and wait for processing to finish. Review the visible subgroups and chapter contents before opening export settings.",
               "Нажмите Assign LP и дождитесь завершения обработки. Проверьте сабгруппы и состав главы до перехода к настройкам экспорта.", "focus_assign.png"),
     )),
    ("05", "Export", "Экспорт",
     "Set the smoothing level, scope and files before exporting. Files are always exported as FBX.",
     "Перед экспортом настройте сглаживание, область экспорта и состав файлов. Файлы всегда экспортируются в FBX.",
     (
         _card("export.settings", "Open Export Settings", "Откройте Export Settings",
               "Click Export Settings to open the export workspace. It contains per-subgroup smoothing on the left and scope, file controls on the right. The full-window screenshot is for orientation; use the detailed cards below for each control.",
               "Нажмите Export Settings: слева откроется сглаживание каждой сабгруппы, справа — область экспорта, файлы. Общий скриншот помогает сориентироваться; ниже есть отдельные карточки для каждой настройки.",
               ((_figure("focus_open_export.png", "Button that opens Export Settings", "Кнопка открытия Export Settings"),),
                (_figure("maya_export_standard.png", "Whole export workspace — double-click to enlarge", "Вся рабочая область — двойной щелчок для увеличения"),))),
         _card("export.smoothing", "Smoothing per subgroup", "Сглаживание сабгрупп",
               "Choose the level in a subgroup row, use plus/minus or the wheel over the level. Shift/Ctrl and drag-selection select several rows so their levels can be changed together. Already-detailed ZBrush HP is not automatically subdivided.",
               "Уровень задаётся в строке сабгруппы, кнопками «+»/«−» или колесом над уровнем. Shift/Ctrl и рамка позволяют выделить несколько строк и поменять уровень вместе. Детализированный ZBrush HP не сглаживается автоматически.",
               ((_figure("Untitled(46).png", "One subgroup and its smoothing level", "Одна сабгруппа и её уровень сглаживания"),),
                (_figure("Untitled(47).png", "Select several rows", "Выделите несколько строк"),
                 _figure("Untitled(48).png", "Adjust them together", "Измените их вместе")))),
         _card("export.smooth", "Smooth View", "Предпросмотр сглаживания",
               "Smooth View previews the selected HP smoothing level in Maya. It helps inspect the result before committing to export.",
               "Smooth View показывает выбранное сглаживание HP в Maya. Так можно проверить результат до экспорта.",
               ((_figure("Untitled(49).png", "Preview HP smoothing", "Предпросмотр сглаживания HP"),
                 _figure("Untitled(50).png", "Leave export settings", "Вернуться из настроек экспорта")),)),
         _card("export.scope", "Choose export scope", "Выберите область экспорта",
               "Export the active chapter, active book or all books. Verify the chosen scope before running a long batch export.",
               "Экспортируйте активную главу, активную книгу или все книги. Перед долгим пакетным экспортом проверьте выбранный вариант.",
               ((_figure("focus_export_scope.png", "Available export scopes", "Возможные области экспорта"),),
                (_figure("Untitled(56).png", "Active chapter", "Активная глава"),
                 _figure("Untitled(58).png", "Active book", "Активная книга")))),
         _card("export.files", "Choose output files", "Выберите состав файлов",
               "Select HP and LP; choose separate files or one HP+LP file. For book/all-books scope, By material and LP in one file become available where supported. LP is triangulated; Standard FBX HP uses its smoothing settings.",
               "Выберите HP и LP; укажите отдельные файлы или общий HP+LP. Для книги/всех книг при поддержке доступны By material и LP in one file. LP триангулируется; HP в Standard FBX использует заданное сглаживание.",
               ((_figure("focus_export_files.png", "Include HP and LP", "Включить HP и LP"),),
                (_figure("focus_export_batch_files.png", "By material / LP in one file", "По материалам / LP одним файлом"),))),
         _card("export.run", "Run export", "Запустите экспорт",
               "Confirm destination, scope and smoothing, then press Export. Do not change the scene until processing completes.",
               "Проверьте папку, область и сглаживание, затем нажмите Export. Не меняйте сцену до завершения обработки.",
               ((_figure("focus_run_export.png", "Start export", "Запустить экспорт"),),)),
     )),
    ("06", "Help and other controls", "Справка и другие команды",
     "Context help, language selection and version information.",
     "Контекстная справка, выбор языка и информация о версии.",
     (
         _card("guide.help", "Context help", "Контекстная справка",
               "1. Click Help in Bake Groups. This instruction card opens and the cursor becomes a question mark.\n\n"
               "2. Click the button, checkbox or section you want explained in the Bake Groups window. The Guide jumps to its card; that click does NOT run the command or change its setting. The question-mark mode then ends.\n\n"
               "3. To ask about another control, click Help again. Right-click to open a context menu, then choose an item to read about it. Press Esc or click Help again to exit.\n\n"
               "On the board: drag with the middle mouse button to pan, use the wheel to zoom, and double-click an image to enlarge it. Close the Guide with the ordinary window X.",
               "1. Нажмите Help в Bake Groups. Откроется эта инструкция, а курсор станет знаком вопроса.\n\n"
               "2. В окне Bake Groups нажмите кнопку, флажок или секцию, о которой хотите узнать. Гайд перейдёт к нужной карточке. Этот щелчок НЕ выполняет команду и НЕ меняет настройку. После выбора режим «?» выключается.\n\n"
               "3. Для справки о другом элементе снова нажмите Help. Чтобы узнать о команде меню, откройте его правым щелчком и выберите пункт. Esc или повторное нажатие Help выключает режим.\n\n"
               "На доске: перемещайте полотно средней кнопкой мыши, масштабируйте колесом, увеличивайте картинки двойным щелчком. Закрывайте гайд обычным крестиком окна.",
               ((_figure("Untitled(86).png", "Help mode off", "Режим справки выключен"),
                 _figure("Untitled(88).png", "Help mode active", "Режим справки активен")),)),
         _card("guide.log", "Log and support", "Журнал и поддержка",
               "Right-click the Log to save a debug log or collect a support package for reporting a problem.",
               "Щёлкните ПКМ по журналу, чтобы сохранить отладочный лог или собрать пакет для сообщения об ошибке.", ()),
         _card("guide.language", "Language and About", "Язык и информация",
               "Language switches the plugin UI. About shows version and update information. Older screenshots can have different button names.",
               "Language переключает язык интерфейса. About показывает версию и сведения об обновлениях. На старых скриншотах названия кнопок могут отличаться.",
               ((_figure("Untitled(55).png", "Language menu", "Меню языка"),),
                (_figure("focus_about_versions.png", "About and update information", "Информация о версии и обновлении"),))),
         _card("guide.install", "Install Bake Groups", "Установка Bake Groups",
               "Keep Install_Bake_Groups.py beside the Bake_Groups folder in a permanent writable location. Select a Shelf in Maya, then drag the installer into the 3D viewport. It creates or updates the BAKE GROUPS button and opens the tool. Choose Save All Shelves. No files are copied elsewhere and userSetup.py is not changed. Keep the folder in place; to move it, run the installer again from the new location. See README_EN.md, README_RU.md, README_JA.md or README_ZH-CN.md beside the installer.",
               "Храните Install_Bake_Groups.py рядом с папкой Bake_Groups в постоянном месте с правом записи. Выберите полку Shelf в Maya и перетащите установщик в 3D-вьюпорт. Он создаст или обновит кнопку BAKE GROUPS и откроет плагин. Выберите Save All Shelves. Файлы не копируются в другие места, userSetup.py не меняется. Не перемещайте папку после установки; для переноса запустите установщик заново из нового места. README на четырёх языках лежат рядом с установщиком.", ()),
     )),
)


def _png_size(name):
    path = os.path.join(os.path.dirname(__file__), "guide_assets", name)
    with open(path, "rb") as stream:
        header = stream.read(24)
    if header[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("Invalid manual image: " + name)
    return struct.unpack(">II", header[16:24])


def _text_height(value, width, point_size, language="en"):
    document = QtGui.QTextDocument()
    document.setDefaultFont(QtGui.QFont(guide_font_family(language), point_size))
    document.setPlainText(value)
    document.setTextWidth(width)
    return document.size().height()


def _media_layout(rows, language):
    """Return relative figure positions and total height for a card's image rows."""
    result = []
    cursor = 0
    for row in rows:
        if not 1 <= len(row) <= 2:
            raise ValueError("A guide image row must contain one or two figures")
        slot_width = (442 - 18 * (len(row) - 1)) / len(row)
        row_figures = []
        for index, (filename, en_caption, ru_caption) in enumerate(row):
            original_w, original_h = _png_size(filename)
            max_height = 520 if len(row) == 1 else 300
            scale = min(2.0, slot_width / original_w, max_height / original_h)
            width, height = original_w * scale, original_h * scale
            caption = (CAPTIONS.get(language, {}).get(en_caption, en_caption)
                       if language in CAPTIONS else
                       ru_caption if language == "ru" else en_caption)
            caption_height = _text_height(caption, slot_width, 11, language) + 12 if caption else 0
            left = index * (slot_width + 18)
            row_figures.append((filename, caption, left, cursor, width, height,
                                caption_height, slot_width))
        caption_band = max(figure[6] for figure in row_figures)
        row_height = caption_band + max(figure[5] for figure in row_figures)
        result.extend((filename, caption, left, top, width, height,
                       caption_band, slot_width)
                      for filename, caption, left, top, width, height, _, slot_width
                      in row_figures)
        cursor += row_height + 20
    return result, max(0, cursor - 20)


def _release_card(language):
    """Highlights for the Maya release."""
    ru = language == "ru"
    translation = RELEASE.get(language, {})
    key = "release.whats_new"
    x, y, width, height = 64, 48, 1200, 740
    items = [{"type": "rect", "x": x, "y": y, "w": width, "h": height,
              "color": "#8d8830", "fill": "#1f2329", "thickness": 3,
              "anchor": key},
             {"type": "rect", "x": x + 24, "y": y + 22, "w": 1152, "h": 86,
              "color": "#09a773", "fill": "#09a773", "thickness": 1},
             {"type": "text", "x": x + 42, "y": y + 32, "w": 1100,
              "text": release_title(language),
              "size": 32, "color": "#f5fff9", "anchor": key}]

    feature_index = [0]

    def feature(column, top, en_heading, ru_heading, en_body, ru_body,
                image=None, image_width=220, image_height=180):
        left = x + 30 + column * 580
        if translation:
            heading, body = translation["features"][feature_index[0]]
            feature_index[0] += 1
        else:
            heading = ru_heading if ru else en_heading
            body = ru_body if ru else en_body
        text_width = 310 if image else 530
        items.append({"type": "text", "x": left, "y": y + top,
                      "w": text_width, "text": heading, "size": 17,
                      "color": "#78d8f7", "anchor": key})
        body_top = top + max(36, _text_height(heading, text_width, 17, language) + 6)
        items.append({"type": "text", "x": left, "y": y + body_top,
                      "w": text_width, "text": body, "size": 12,
                      "color": "#e0e8ee", "anchor": key})
        if image:
            original_w, original_h = _png_size(image)
            scale = min(image_width / original_w, image_height / original_h)
            items.append({"type": "image", "x": left + 330, "y": y + top + 2,
                          "w": original_w * scale, "h": original_h * scale,
                          "asset": image, "anchor": key})

    feature(0, 137, "Mesh Check", "Проверка мешей",
            "Review transforms, duplicates, ZBrush candidates, disconnected geometry and empty transforms in the chapter tab. Select findings, apply fixes or skip specific checks or meshes.",
            "Проверяйте трансформации, дубликаты, кандидатов ZBrush, несвязанную геометрию и пустые узлы во вкладке главы. Выделяйте находки, исправляйте их или задавайте исключения.",
            "focus_check_results.png", 205, 158)
    feature(1, 137, "Chapters by LP material", "Главы по материалам LP",
            "Multi-material LP is split by face material. HP ownership uses distances to the actual LP chapter surfaces. Long checks show progress and can be canceled.",
            "LP с несколькими материалами делится по полигонам. Принадлежность HP определяется по расстоянию до поверхности LP каждой главы. Длительные проверки показывают ход работы и поддерживают отмену.")
    feature(0, 332, "Assign LP", "Назначение LP",
            "LP matching uses topology and geometry to verify its HP groups. Review assignments after HP analysis, especially in chapters with multiple material slots.",
            "Сопоставление LP с HP использует топологию и геометрию. После анализа HP проверьте назначения, особенно в главах с несколькими слотами материалов.")
    feature(1, 332, "Color HP palette", "Палитра Color HP",
            "Color HP updates changed meshes. Edit the active chapter’s colors beside subgroup names, save named presets and choose a default palette.",
            "Color HP обновляет цвета изменившихся мешей. Меняйте цвета рядом с названиями сабгрупп, создавайте пресеты и выбирайте палитру по умолчанию.",
            "focus_palette_colors.png", 210, 146)
    feature(0, 520, "Interactive Help and illustrated guide", "Контекстная справка и иллюстрации",
            "In Help mode, right-click to open a menu and select a command to read about it. The guide uses cropped images and highlights to locate controls.",
            "В режиме Help откройте меню через ПКМ и выберите команду для справки. В гайде нужные элементы выделены на кадрированных снимках.")
    feature(1, 520, "Export controls", "Настройки экспорта",
            "Exclude individual chapters from export, and use Freeze LP normals to preserve LP normals on the temporary triangulated export copies.",
            "Исключайте отдельные главы из экспорта. Фриз нормалей LP сохраняет нормали на временных экспортных копиях при триангуляции.",
            "focus_export_files.png", 205, 125)
    return items, y + height + 40


def build_document(language):
    guide_font_family(language)
    russian = language == "ru"
    translated_sections = TRANSLATED_SECTIONS.get(language, {})
    translated_cards = CARDS.get(language, {})
    items, y = _release_card(language)
    navy = "#242d39"
    for number, en_heading, ru_heading, en_intro, ru_intro, cards in SECTIONS:
        heading, intro = translated_sections.get(
            number, (ru_heading, ru_intro) if russian else (en_heading, en_intro))
        items.append({"type": "rect", "x": 64, "y": y, "w": 1040, "h": 100,
                      "color": "#315775", "fill": "#1c3448", "thickness": 2})
        items.append({"type": "text", "x": 88, "y": y + 8, "w": 980,
                      "text": number + "  " + heading, "size": 23, "color": "#f0f5ff"})
        items.append({"type": "text", "x": 88, "y": y + 56, "w": 980,
                      "text": intro, "size": 13, "color": "#c4d7e8"})
        y += 122
        column_y = [y, y]
        for index, card in enumerate(cards):
            key, en_title, ru_title, en_body, ru_body, rows = card
            column = index % 2
            x, card_y = 64 + column * 530, column_y[column]
            title, body = translated_cards.get(
                key, (ru_title, ru_body) if russian else (en_title, en_body))
            title_height = _text_height(title, 470, 18, language)
            body_y = 15 + title_height + 12
            media_y = body_y + _text_height(body, 470, 12, language) + 18
            figures, media_height = _media_layout(rows, language)
            height = max(275, media_y + media_height + 32)
            items.append({"type": "rect", "x": x, "y": card_y, "w": 510, "h": height,
                          "color": "#526b83", "fill": navy, "thickness": 2,
                          "anchor": key})
            items.append({"type": "text", "x": x + 20, "y": card_y + 15, "w": 470,
                          "text": title, "size": 18, "color": "#f1f5fb", "anchor": key})
            items.append({"type": "text", "x": x + 20, "y": card_y + body_y, "w": 470,
                          "text": body, "size": 12, "color": "#d0dce8", "anchor": key})
            for filename, caption, left, top, image_w, image_h, caption_height, slot_width in figures:
                image_x = x + 20 + left
                figure_y = card_y + media_y + top
                if caption:
                    items.append({"type": "text", "x": image_x, "y": figure_y,
                                  "w": slot_width,
                                  "text": caption, "size": 11,
                                  "color": "#75d4f6", "anchor": key})
                items.append({"type": "image", "x": image_x,
                              "y": figure_y + caption_height,
                              "w": image_w, "h": image_h,
                              "asset": filename, "anchor": key})
            column_y[column] += height + 22
        y = max(column_y)
        y += 34
    return {"schema": 1, "language": language, "items": items, "manual_version": 11,
            "help_card_revision": HELP_CARD_REVISION}
