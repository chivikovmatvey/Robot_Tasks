# Сюда копируем твои существующие скрипты:
#   scanner.py, adapt.py, clean.py, inject.py, fix_anchors.py, translate.py
# А также конфиги:
#   clean_rules.json, geos.json
#
# В следующих этапах эти файлы будут импортироваться из main.py:
#   from scripts import inject, clean, adapt, scanner
#
# Чтобы скрипты работали и как модули FastAPI, и как старые батники одновременно,
# мы добавим в каждый "не-интерактивный" режим без изменения существующей логики.
