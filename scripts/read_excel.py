import zipfile
import xml.etree.ElementTree as ET

path = r"d:\buku zis\summary-2026-07-02__2026-07-02 (1).xlsx"
with zipfile.ZipFile(path, 'r') as zip_ref:
    print("Files in zip:", zip_ref.namelist())
    if "xl/worksheets/sheet1.xml" in zip_ref.namelist():
        sheet1_xml = zip_ref.read("xl/worksheets/sheet1.xml")
        print("\nsheet1.xml length:", len(sheet1_xml))
        print("sheet1.xml beginning:", sheet1_xml[:1000])
    if "xl/sharedStrings.xml" in zip_ref.namelist():
        ss_xml = zip_ref.read("xl/sharedStrings.xml")
        print("\nsharedStrings.xml length:", len(ss_xml))
        print("sharedStrings.xml beginning:", ss_xml[:1000])
