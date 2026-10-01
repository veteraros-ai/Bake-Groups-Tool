# -*- coding: utf-8 -*-
"""Bundled visual Maya guide. Pur_0 screenshots are layout references only."""
from __future__ import absolute_import, division, print_function

import os
import struct

try:
    import bg_version
    CURRENT_VERSION = bg_version.__version__
except ImportError:
    CURRENT_VERSION = ""

try:
    from PySide6 import QtGui
except ImportError:
    from PySide2 import QtGui


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
               "Use ? Help on any control to jump to its explanation. Double-click an image to enlarge it. Screenshots show the original interface; button positions may differ in this version.",
               "Нажмите ? Help, затем элемент интерфейса — доска перейдёт к пояснению. Дважды щёлкните по изображению, чтобы увеличить его. На скриншотах исходный интерфейс; расположение кнопок могло измениться.",
               ((_figure("Untitled.png", "Choose the HP and LP roots", "Выбор корней HP и LP"),),
                (_figure("Untitled(85).png", "Original full window — enlarge with a double-click", "Исходное окно целиком — увеличьте двойным щелчком"),))),
         _card("pick.hp", "Choose the HP root", "Выберите корень HP",
               "Select the high-poly group in Maya and click Pick HP. Keep the hierarchy intact until the chapter is created.",
               "Выделите группу high-poly в Maya и нажмите Pick HP. Сохраняйте иерархию до создания главы.", "Untitled.png"),
         _card("pick.lp", "Choose the LP root", "Выберите корень LP",
               "Select the low-poly group and click Pick LP. LP meshes are the targets for matching and baking.",
               "Выделите группу low-poly и нажмите Pick LP. LP-меши — цели сопоставления и запекания.", "Untitled.png"),
         _card("chapter.create", "Create chapters", "Создайте главы",
               "Create builds the chapter. For several LP materials choose one chapter (split later during Analyze HP) or several chapters immediately. Keep HP preserves existing HP grouping when adding new roots.",
               "Create создаёт главу. При нескольких материалах LP выберите одну главу (разделение позже при Analyze HP) или сразу несколько. Keep HP сохраняет распределение HP при добавлении новых корней.",
               ((_figure("Untitled.png", "Pick roots, then Create", "Выберите корни, затем Create"),),
                (_figure("Untitled(1).png", "Choice for multiple LP materials", "Выбор при нескольких материалах LP"),),
                (_figure("Untitled(2).png", "One chapter", "Одна глава"),
                 _figure("Untitled(3).png", "Several chapters in a book", "Несколько глав в книге")),
                (_figure("Untitled(4).png", "Keep HP when adding more roots", "Keep HP при добавлении новых корней"),))),
         _card("toc", "Books and chapters", "Книги и главы",
               "The Table of Contents shows books and chapters. Select a chapter to work with it; double-click a book name to rename it. The eye toggles visibility.",
               "Оглавление показывает книги и главы. Выберите главу для работы; двойным щелчком можно переименовать книгу. Глаз переключает видимость.",
               ((_figure("Untitled(33).png", "Book with several chapters", "Книга с несколькими главами"),),
                (_figure("Untitled(41).png", "Edit a book name", "Переименование книги"),))),
         _card("toc.menu", "Chapter context menu", "Меню главы",
               "Right-click a chapter: select its meshes, move selected meshes to it, rename it, find a matching HP for LP, put it in a new/existing book or extract it. Deleting the chapter leaves meshes in Maya.",
               "Правый щелчок по главе: выделение её мешей, перенос выделенных, переименование, поиск HP для LP, помещение в новую/существующую книгу или извлечение. Удаление главы оставляет меши в Maya.",
               ((_figure("Untitled(42).png", "Chapter context menu", "Контекстное меню главы"),),
                (_figure("Untitled(34).png", "Select meshes", "Выделить меши"),
                 _figure("Untitled(35).png", "Move selected meshes", "Перенести выделенные меши")),
                (_figure("Untitled(38).png", "Create a book", "Создать книгу"),
                 _figure("Untitled(40).png", "Extract from book", "Извлечь из книги")))),
         _card("toc.find_lost", "Find Lost (beta)", "Find Lost (beta)",
               "Select an HP or LP mesh first. Find Lost searches for matching opposite meshes and can move found meshes into the chapter. Because it changes grouping, inspect the result afterward.",
               "Сначала выделите HP- или LP-меш. Find Lost ищет подходящие меши противоположного типа и может перенести найденное в главу. Поскольку группировка меняется, затем проверьте результат.",
               ((_figure("Untitled(37).png", "Chapter menu command", "Команда в меню главы"),),
                (_figure("Untitled(42).png", "Where to find it", "Где находится команда"),))),
     )),
    ("02", "Prepare and analyze HP", "Подготовка и анализ HP",
     "Check geometry and transforms first, then run Analyze HP and inspect the distribution.",
     "Сначала проверьте геометрию и трансформации, затем запустите Analyze HP и проверьте распределение.",
     (
         _card("hp.prepare.tools", "Combine and Separate", "Combine и Separate",
               "Combine merges selected meshes; Separate splits a combined mesh into independent shells. Use these preparation tools only when the model needs it, then recheck the resulting geometry before Analyze HP.",
               "Combine объединяет выделенные меши; Separate разделяет объединённый меш на самостоятельные части. Используйте их при необходимости и проверьте результат до Analyze HP.",
               ((_figure("Untitled(4).png", "Preparation toolbar", "Панель подготовки"),),
                (_figure("Untitled(7).png", "Combined-mesh warning", "Предупреждение об объединённом меше"),))),
         _card("hp.prepare.combine", "Combine meshes", "Объединить меши",
               "Combine joins selected mesh transforms into one mesh. Use it only when those parts should be treated as one HP object; inspect the result before Analyze HP.",
               "Combine объединяет выделенные меши в один объект. Используйте его, только если эти части должны обрабатываться как единый HP-меш; затем проверьте результат до Analyze HP.",
               ((_figure("Untitled(4).png", "Combine is the first preparation button", "Combine — первая кнопка подготовки"),),)),
         _card("hp.prepare.separate", "Separate mesh shells", "Разделить меш на части",
               "Separate splits a selected combined mesh into independent mesh parts when possible. The combined-mesh warning can point to candidates; inspect the split result and re-run checks.",
               "Separate разделяет выделенный объединённый меш на самостоятельные части, если это возможно. Предупреждение об объединённых мешах поможет найти кандидатов; проверьте части и повторите проверку.",
               ((_figure("Untitled(4).png", "Separate is the second preparation button", "Separate — вторая кнопка подготовки"),),
                (_figure("Untitled(7).png", "Example warning", "Пример предупреждения"),))),
         _card("hp.check", "Check before analysis", "Проверка перед анализом",
               "Before Analyze HP you can combine, separate and find ZBrush geometry. The check tool warns about duplicate, combined or invalid-transform meshes; inspect each warning before continuing.",
               "До Analyze HP можно объединить или разделить меши и найти ZBrush-геометрию. Проверка предупредит о дубликатах, объединённых мешах и трансформациях; просмотрите каждое предупреждение.",
               ((_figure("Untitled(5).png", "Preparation tools above Analyze HP", "Инструменты подготовки над Analyze HP"),),
                (_figure("Untitled(7).png", "Combined meshes warning", "Предупреждение об объединённых мешах"),),
                (_figure("Untitled(9).png", "Invalid transforms warning", "Предупреждение о трансформациях"),))),
         _card("hp.check.duplicates", "Duplicate meshes", "Дубликаты мешей",
               "Inspect duplicate candidates. Remove only redundant copies after verifying which mesh belongs in the scene.",
               "Проверьте найденные дубликаты. Удаляйте лишние копии только после проверки того, какой меш нужен в сцене.", "Untitled(6).png"),
         _card("hp.check.transforms", "Freeze Transformations", "Freeze Transformations",
               "The root groups and meshes must have valid transforms. If prompted, inspect the listed nodes before freezing them; re-run the check afterward.",
               "У корневых групп и мешей должны быть допустимые трансформации. Перед Freeze проверьте перечисленные узлы и затем повторите проверку.", "Untitled(9).png"),
         _card("hp.zbrush", "ZBrush geometry", "Геометрия ZBrush",
               "Find ZBrush marks already-detailed HP meshes. Right-click its button to set the triangle threshold, run the search now or add selected meshes to the ZBrush layer. Such meshes are not automatically smoothed at export.",
               "Find ZBrush помечает уже детализированные HP-меши. Правый щелчок по кнопке позволяет задать порог треугольников, запустить поиск или добавить выделенные меши в ZBrush-слой. При экспорте они не сглаживаются автоматически.",
               ((_figure("Untitled(11).png", "Search and threshold controls", "Поиск и настройка порога"),),
                (_figure("Untitled(8).png", "Possible ZBrush mesh warning", "Предупреждение о возможном ZBrush-меше"),))),
         _card("hp.color", "Color HP by subgroup", "Окраска HP по сабгруппам",
               "Enable Color HP to see the HP analysis as different viewport colors by subgroup. This is a visual inspection aid, not an exported material assignment.",
               "Включите Color HP, чтобы видеть во вьюпорте разные цвета HP-сабгрупп. Это помогает проверить анализ; цвета не заменяют материалы для экспорта.",
               ((_figure("Untitled(4).png", "Color HP checkbox", "Флажок Color HP"),),
                (_figure("Untitled(71).png", "Choose a subgroup color when needed", "При необходимости выберите цвет сабгруппы"),))),
         _card("hp.analyze", "Analyze HP", "Анализ HP",
               "Run Analyze HP after preparing the scene. The tool distributes HP meshes into subgroups; visually check the result and correct any misplaced meshes before export.",
               "После подготовки сцены нажмите Analyze HP. Плагин распределит HP по сабгруппам; проверьте результат и исправьте ошибочно распределённые меши до экспорта.",
               ((_figure("Untitled(10).png", "Start analysis", "Запустить анализ"),),
                (_figure("Untitled(30).png", "Resulting subgroup list", "Полученный список сабгрупп"),))),
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
               ((_figure("Untitled(75).png", "Strategy selector in the Algorithm panel", "Выбор стратегии в панели Algorithm"),),)),
         _card("hp.algorithm.optimization", "Analysis optimization", "Оптимизация анализа",
               "Optimal retains more geometric detail for matching; Speed uses a tighter sample cap on very dense meshes to reduce analysis time. The option does not change source geometry or exported mesh smoothing.",
               "Optimal сохраняет больше геометрических данных для сопоставления; Speed сильнее ограничивает выборку очень плотных мешей ради скорости анализа. Параметр не меняет исходную геометрию и сглаживание при экспорте.",
               ((_figure("Untitled(75).png", "Optimization selector", "Выбор режима оптимизации"),),)),
         _card("hp.algorithm.collision", "Collision tolerance", "Допуск Collision",
               "Collision (%) changes the geometric matching tolerance. Lower values are stricter and may split more HP into separate groups; higher values are looser and may merge unrelated parts. Change gradually and inspect the result.",
               "Collision (%) меняет допуск геометрического сопоставления. Меньшие значения строже и могут разделить HP на большее число групп; большие значения мягче и могут объединить лишнее. Меняйте постепенно и проверяйте результат.",
               ((_figure("Untitled(76).png", "Collision control", "Параметр Collision"),),)),
         _card("hp.algorithm.floaters", "Ignore Floaters", "Ignore Floaters",
               "When Ignore Floaters is checked, the automatic floater-detection pass is skipped. Uncheck it only when that pass is needed, then verify that bolts with their own LP are not assigned as floaters.",
               "При включённом Ignore Floaters автоматический этап распознавания флоатеров пропускается. Выключайте флажок только когда этот этап нужен, а затем проверьте, что болты с собственной LP не стали флоатерами.",
               ((_figure("Untitled(75).png", "Ignore Floaters in Algorithm", "Ignore Floaters в панели Algorithm"),),)),
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
               ((_figure("Untitled(20).png", "Enter name and create", "Имя и создание"),),
                (_figure("Untitled(21).png", "New subgroup row", "Строка новой сабгруппы"),))),
         _card("groups.row", "Subgroup controls", "Управление сабгруппой",
               "The row contains visibility, name, Move selected meshes, Lock and Delete. Double-click the name to select its meshes; right-click for Rename and Select Color. Lock protects a manual group during later HP analysis. Delete moves its meshes to the chapter root, not out of Maya.",
               "В строке есть видимость, имя, перенос выделенных мешей, замок и удаление. Двойной щелчок по имени выделяет меши; правый открывает Rename и Select Color. Замок защищает ручную группировку при повторном анализе HP. Удаление переносит меши в корень главы, не удаляя их из Maya.",
               ((_figure("Untitled(21).png", "Subgroup row", "Строка сабгруппы"),),
                (_figure("Untitled(70).png", "Rename and color menu", "Меню имени и цвета"),
                 _figure("Untitled(27).png", "Rename dialog", "Окно переименования")),
                (_figure("Untitled(71).png", "Choose subgroup color", "Выбор цвета сабгруппы"),),
                (_figure("Untitled(28).png", "Hidden subgroup", "Скрытая сабгруппа"),
                 _figure("Untitled(29).png", "Locked subgroup", "Заблокированная сабгруппа")))),
         _card("hp.find_similar", "Find similar meshes", "Поиск похожих мешей",
               "Find Sim searches for matching HP meshes as a combined whole. Right-click the button for Find All: it searches for each of several selected meshes separately. Compare results with LP and subgroups before moving bolts.",
               "Find Sim ищет похожие HP-меши, рассматривая выделенное как единое целое. Правый щелчок открывает Find All: он ищет каждый из нескольких выделенных мешей отдельно. Перед переносом болтов сравните результат с LP и сабгруппами.",
               ((_figure("Untitled(31).png", "Find Sim — combined selection", "Find Sim — выделение как единое целое"),
                 _figure("Untitled(32).png", "Find All — separate selected meshes", "Find All — отдельный поиск каждого меша")),)),
         _card("groups.search", "Find a mesh or clean empty groups", "Поиск меша и пустые группы",
               "Right-click an empty area of the subgroup list. Find group by mesh locates the subgroup containing the selected Maya mesh; Delete empty groups removes subgroup entries without meshes.",
               "Щёлкните правой кнопкой по пустому месту списка. Find group by mesh найдёт сабгруппу выделенного в Maya меша; Delete empty groups уберёт пустые записи сабгрупп.",
               ((_figure("Untitled(72).png", "List context menu", "Контекстное меню списка"),),
                (_figure("Untitled(73).png", "Delete empty groups", "Удалить пустые группы"),
                 _figure("Untitled(74).png", "Find group by mesh", "Найти группу по мешу")))),
     )),
    ("04", "Assign LP", "Назначение LP",
     "After HP grouping, associate LP with the subgroups and inspect the result.",
     "После группировки HP сопоставьте LP с сабгруппами и проверьте результат.",
     (
         _card("lp.assign", "Assign LP meshes", "Назначить LP-меши",
               "Click Assign LP and wait for processing to finish. Review the visible subgroups and chapter contents before opening export settings.",
               "Нажмите Assign LP и дождитесь завершения обработки. Проверьте сабгруппы и состав главы до перехода к настройкам экспорта.", "Untitled(44).png"),
     )),
    ("05", "Export", "Экспорт",
     "Set the smoothing level, scope and files before exporting. Standard FBX is the default target.",
     "Перед экспортом настройте сглаживание, область экспорта и состав файлов. По умолчанию используется Standard FBX.",
     (
         _card("export.settings", "Open Export Settings", "Откройте Export Settings",
               "Click Export Settings to open the export workspace. It contains per-subgroup smoothing on the left and scope, file and Cage controls on the right. The full-window screenshot is for orientation; use the detailed cards below for each control.",
               "Нажмите Export Settings: слева откроется сглаживание каждой сабгруппы, справа — область экспорта, файлы и Cage. Общий скриншот помогает сориентироваться; ниже есть отдельные карточки для каждой настройки.",
               ((_figure("Untitled(45).png", "Button that opens Export Settings", "Кнопка открытия Export Settings"),),
                (_figure("Untitled(68).png", "Whole export workspace — double-click to enlarge", "Вся рабочая область — двойной щелчок для увеличения"),))),
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
               ((_figure("Untitled(57).png", "Available export scopes", "Возможные области экспорта"),),
                (_figure("Untitled(56).png", "Active chapter", "Активная глава"),
                 _figure("Untitled(58).png", "Active book", "Активная книга")))),
         _card("export.target", "Export target", "Целевой формат экспорта",
               "Standard FBX exports meshes from Maya with the selected smoothing and file settings. Marmoset Toolbag is a separate bridge workflow and is not part of this Maya guide.",
               "Standard FBX экспортирует меши из Maya с выбранными настройками сглаживания и файлов. Marmoset Toolbag — отдельный процесс через мост; он не входит в этот мануал Maya.",
               ((_figure("Untitled(80).png", "Target selector", "Выбор целевого формата"),),)),
         _card("export.files", "Choose output files", "Выберите состав файлов",
               "Select HP, LP and optionally Cage; choose separate files or one HP+LP file. For book/all-books scope, By material and LP in one file become available where supported. LP is triangulated; Standard FBX HP uses its smoothing settings.",
               "Выберите HP, LP и при необходимости Cage; укажите отдельные файлы или общий HP+LP. Для книги/всех книг при поддержке доступны By material и LP in one file. LP триангулируется; HP в Standard FBX использует заданное сглаживание.",
               ((_figure("Untitled(62).png", "Include HP, LP and Cage", "Включить HP, LP и Cage"),
                 _figure("Untitled(63).png", "Separate or one HP+LP file", "Отдельно или общий HP+LP")),
                (_figure("Untitled(65).png", "By material / LP in one file", "По материалам / LP одним файлом"),))),
         _card("export.run", "Run export", "Запустите экспорт",
               "Confirm target, destination, scope and smoothing, then press Export. Do not change the scene until processing completes.",
               "Проверьте формат, папку, область и сглаживание, затем нажмите Export. Не меняйте сцену до завершения обработки.",
               ((_figure("Untitled(80).png", "Standard FBX export target", "Целевой формат Standard FBX"),),
                (_figure("Untitled(51).png", "Start export", "Запустить экспорт"),))),
     )),
    ("06", "Cage and other controls", "Cage и другие команды",
     "Optional cage controls and general help. Cage behavior is documented separately from the basic workflow.",
     "Дополнительные настройки Cage и общие команды. Работа с Cage отделена от базового процесса.",
     (
         _card("cage.controls", "Cage controls", "Управление Cage",
               "Create, edit or display Cage with the top buttons; inspect intersections, export separately or delete it with the lower buttons. Expansion and Normal move are distinct displacement controls. Selected or hidden subgroups affect which cages receive the adjustment.",
               "Верхние кнопки создают, редактируют и показывают Cage; нижние ищут пересечения, экспортируют отдельно или удаляют Cage. Expansion и Normal move — разные способы смещения. Выделенные или скрытые сабгруппы влияют на то, к каким Cage применяется изменение.",
               ((_figure("Untitled(52).png", "Complete Cage panel", "Полная панель Cage"),),
                (_figure("Untitled(53).png", "Create / edit / display", "Создать / редактировать / показать"),
                 _figure("Untitled(54).png", "Intersections / export / delete", "Пересечения / экспорт / удаление")))),
         _card("cage.inflation", "Cage expansion and export", "Расширение и экспорт Cage",
               "Expansion (inflate) and Normal move adjust the Cage in different directions. A selection limits the adjustment to selected subgroups or Cage meshes; hidden subgroups are skipped. Check Export cage with chapter if the Cage should accompany HP/LP output.",
               "Expansion (inflate) и Normal move смещают Cage разными способами. Выделение ограничивает изменение выбранными сабгруппами или Cage-мешами; скрытые сабгруппы пропускаются. Включите Export cage with chapter, если Cage нужен вместе с HP/LP.",
               ((_figure("Untitled(52).png", "Expansion, Normal move and export flag", "Expansion, Normal move и флажок экспорта"),),
                (_figure("Untitled(62).png", "Include Cage in the output", "Включить Cage в результат экспорта"),))),
         _card("guide.help", "Context help", "Контекстная справка",
               "Click ? Help, then a Bake Groups control. This Guide opens the related card. Close it with the standard window X.",
               "Нажмите ? Help, затем элемент Bake Groups. Guide откроет нужную карточку. Закрывайте его обычным крестиком окна.",
               ((_figure("Untitled(86).png", "Help mode off", "Режим справки выключен"),
                 _figure("Untitled(88).png", "Help mode active", "Режим справки активен")),)),
         _card("guide.language", "Language and About", "Язык и информация",
               "Language switches the plugin UI. About shows version and update information. Older screenshots can have different button names.",
               "Language переключает язык интерфейса. About показывает версию и сведения об обновлениях. На старых скриншотах названия кнопок могут отличаться.",
               ((_figure("Untitled(55).png", "Language menu", "Меню языка"),),
                (_figure("Untitled(79).png", "About and update information", "Информация о версии и обновлении"),))),
     )),
)


def _png_size(name):
    path = os.path.join(os.path.dirname(__file__), "guide_assets", name)
    with open(path, "rb") as stream:
        header = stream.read(24)
    if header[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("Invalid manual image: " + name)
    return struct.unpack(">II", header[16:24])


def _text_height(value, width, point_size):
    document = QtGui.QTextDocument()
    document.setDefaultFont(QtGui.QFont("Arial", point_size))
    document.setPlainText(value)
    document.setTextWidth(width)
    return document.size().height()


def _media_layout(rows, russian):
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
            caption = ru_caption if russian else en_caption
            caption_height = _text_height(caption, slot_width, 11) + 12 if caption else 0
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
    """A single-screen release card with the five supplied UI screenshots."""
    ru = language == "ru"
    key = "release.whats_new"
    x, y, width, height = 64, 48, 1200, 740
    items = [{"type": "rect", "x": x, "y": y, "w": width, "h": height,
              "color": "#8d8830", "fill": "#1f2329", "thickness": 3,
              "anchor": key},
             {"type": "rect", "x": x + 24, "y": y + 22, "w": 1152, "h": 86,
              "color": "#09a773", "fill": "#09a773", "thickness": 1},
             {"type": "text", "x": x + 42, "y": y + 32, "w": 1100,
              "text": ("Что нового в версии " if ru else "New in version ") + CURRENT_VERSION,
              "size": 32, "color": "#f5fff9", "anchor": key}]

    def feature(column, top, en_heading, ru_heading, en_body, ru_body,
                image=None, image_width=220, image_height=180):
        left = x + 30 + column * 580
        heading = ru_heading if ru else en_heading
        body = ru_body if ru else en_body
        text_width = 310 if image else 530
        items.append({"type": "text", "x": left, "y": y + top,
                      "w": text_width, "text": heading, "size": 17,
                      "color": "#78d8f7", "anchor": key})
        items.append({"type": "text", "x": left, "y": y + top + 36,
                      "w": text_width, "text": body, "size": 12,
                      "color": "#e0e8ee", "anchor": key})
        if image:
            original_w, original_h = _png_size(image)
            scale = min(image_width / original_w, image_height / original_h)
            items.append({"type": "image", "x": left + 330, "y": y + top + 2,
                          "w": original_w * scale, "h": original_h * scale,
                          "asset": image, "anchor": key})

    feature(0, 137, "Context help", "Контекстная справка",
            "Click ? Help, then a control to see its explanation. Pan the board with the middle mouse button; zoom with the wheel.",
            "Нажмите ? Help, затем элемент интерфейса, чтобы открыть его описание. Перемещайте доску средней кнопкой мыши, масштабируйте колесом.",
            "release_help.png", 220, 150)
    feature(1, 137, "Standard FBX", "Standard FBX",
            "HP smoothing runs outside Maya. Heavy meshes may export much faster; the smoothing level is still set per subgroup.",
            "HP сглаживается вне Maya. Тяжёлые меши могут экспортироваться заметно быстрее; уровень задаётся для каждой сабгруппы.",
            "release_standard_fbx.png", 190, 185)
    feature(0, 337, "Marmoset Toolbag", "Marmoset Toolbag",
            "Export a Bake Groups package, then open Toolbag for automatic import. HP smoothing is applied in Toolbag.",
            "Экспортируйте пакет Bake Groups и откройте Toolbag для автоматического импорта. Сглаживание HP применяется в Toolbag.",
            "release_marmoset_target.png", 190, 185)
    feature(1, 337, "Marmoset Bridge Setup", "Настройка моста Marmoset",
            "Install Bridge adds the plugin to Toolbag. Export Package Only saves the package without installing it.",
            "Install Bridge добавляет плагин в Toolbag. Export Package Only сохраняет пакет без установки моста.",
            "release_bridge_setup.png", 220, 155)
    feature(0, 537, "Bake Groups Bridge", "Bake Groups Bridge",
            "Open / Sync Package imports the meshes. Set Smoothing Level, apply it to selected HP, or toggle HP and LP visibility.",
            "Open / Sync Package импортирует меши. Задайте Smoothing Level для выбранных HP или переключайте видимость HP и LP.",
            "release_bridge_controls.png", 220, 155)
    feature(1, 537, "Cage", "Cage",
            "Cage export starts off. Create Cage enables it after a successful build; deleting the Cage disables it again.",
            "Экспорт Cage сначала выключен. Create Cage включает его после успешного создания; удаление Cage снова выключает флаг.")
    return items, y + height + 40


def build_document(language):
    russian = language == "ru"
    items, y = _release_card(language)
    navy = "#242d39"
    for number, en_heading, ru_heading, en_intro, ru_intro, cards in SECTIONS:
        heading = ru_heading if russian else en_heading
        intro = ru_intro if russian else en_intro
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
            title = ru_title if russian else en_title
            body = ru_body if russian else en_body
            title_height = _text_height(title, 470, 18)
            body_y = 15 + title_height + 12
            media_y = body_y + _text_height(body, 470, 12) + 18
            figures, media_height = _media_layout(rows, russian)
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
    return {"schema": 1, "language": language, "items": items, "manual_version": 5}
