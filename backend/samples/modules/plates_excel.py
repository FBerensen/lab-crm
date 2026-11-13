"""Pure XLSX codec. Does not import Django or write to a database."""
from decimal import Decimal, InvalidOperation
from io import BytesIO
from zipfile import ZipFile, BadZipFile
import re
from defusedxml.ElementTree import fromstring
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill, Border, Side
from openpyxl.comments import Comment

ROWS="ABCDEFGH"
OPTIONAL=("ДНК, мкл","Вода, мкл","Примечания","Типы")
MAX_BYTES=2*1024*1024
MAX_UNPACKED=20*1024*1024

class ExcelError(ValueError):pass

def value_text(v):
    if v is None:return ""
    if hasattr(v,"isoformat"):return v.isoformat()
    return str(v)

def quantity(value,where):
    if value is None or isinstance(value,str) and not value.strip():return None
    if isinstance(value,bool):raise ExcelError(where+": требуется число, не логическое значение.")
    try:d=Decimal(str(value).strip().replace(",","."))
    except (InvalidOperation,ValueError):raise ExcelError(where+": некорректный объём.")
    if not d.is_finite() or d<0 or d>=Decimal("1000000000000") or d!=d.quantize(Decimal("0.0001")):
        raise ExcelError(where+": требуется конечное неотрицательное число, до 4 знаков после запятой и до 12 целых цифр.")
    return format(d.normalize(),"f")

def checked_book(data):
    if not data or len(data)>MAX_BYTES:raise ExcelError("Файл пуст или превышает 2 МБ.")
    try:
        with ZipFile(BytesIO(data)) as z:
            items=z.infolist();names=[i.filename for i in items]
            if len(items)>256 or len(names)!=len(set(names)) or sum(i.file_size for i in items)>MAX_UNPACKED:
                raise ExcelError("Превышены ограничения структуры XLSX.")
            if "[Content_Types].xml" not in names:raise ExcelError("Это не книга XLSX.")
            for item in items:
                low=item.filename.lower()
                if item.flag_bits&1 or ".." in item.filename.split("/") or low.startswith("/") or "vbaproject" in low or "externallinks/" in low or low.endswith("connections.xml"):
                    raise ExcelError("Макросы, внешние связи, шифрование и небезопасные пути запрещены.")
                if low.endswith(".rels"):
                    xml=fromstring(z.read(item))
                    if any(node.attrib.get("TargetMode","").lower()=="external" for node in xml):raise ExcelError("Внешние ссылки запрещены.")
            content=z.read("[Content_Types].xml").lower()
            if b"macroenabled" in content or b"vbaproject" in content:raise ExcelError("Допускается только XLSX без макросов.")
        book=load_workbook(BytesIO(data),read_only=False,data_only=False,keep_links=False)
        if len(book.worksheets)>8:raise ExcelError("Слишком много листов.")
        for sheet in book:
            if sheet.max_row>64 or sheet.max_column>32:raise ExcelError("Лист превышает допустимый размер: "+sheet.title)
            for row in sheet:
                for cell in row:
                    if cell.data_type in {"f","e"}:raise ExcelError(sheet.title+" / "+cell.coordinate+": формулы и ошибки Excel запрещены.")
        return book
    except ExcelError:raise
    except Exception as exc:raise ExcelError("Не удалось прочитать безопасную книгу XLSX.") from exc

def grid_headers(sheet):
    for cells in sheet:
        for cell in cells:
            allowed=(1<=cell.row<=5 and cell.column in (1,2)) or (cell.row==7 and 2<=cell.column<=13) or (8<=cell.row<=15 and 1<=cell.column<=13)
            if cell.value is not None and cell.value!="" and not allowed:
                raise ExcelError(sheet.title+" / "+cell.coordinate+": данные вне областей шаблона.")
    if any(sheet.cell(7,c+1).value!=c or isinstance(sheet.cell(7,c+1).value,bool) for c in range(1,13)):
        raise ExcelError(sheet.title+": ожидаются номера 1–12 в B7:M7.")
    if any(sheet.cell(r+8,1).value!=letter for r,letter in enumerate(ROWS)):
        raise ExcelError(sheet.title+": ожидаются ряды A–H в A8:A15.")
    for merge in sheet.merged_cells.ranges:
        if merge.min_row<=15 and merge.max_row>=8 and merge.min_col<=13 and merge.max_col>=2:
            raise ExcelError(sheet.title+": объединения внутри сетки запрещены.")

def parse(data,default_dna,default_water,samples):
    book=checked_book(data)
    try:
        candidates=[s for s in book if s.title not in OPTIONAL and s.title not in {"_CRM","Инструкция"}]
        if len(candidates)!=1:raise ExcelError("Нужен один основной лист исходного блока.")
        main=candidates[0];grid_headers(main)
        if "_CRM" in book and (book["_CRM"]["A1"].value!="format" or book["_CRM"]["B1"].value!="LABCRM_8G_1"):
            raise ExcelError("Неизвестная версия служебного формата XLSX.")
        for name in OPTIONAL:
            if name in book:grid_headers(book[name])
        common_dna=quantity(main["B4"].value,"B4 / ДНК")
        common_water=quantity(main["B5"].value,"B5 / вода")
        if common_dna is None:common_dna=quantity(default_dna,"Общий объём ДНК блока")
        if common_water is None:common_water=quantity(default_water,"Общий объём воды блока")
        if common_dna is None or common_water is None:raise ExcelError("Не заданы общие объёмы ДНК и воды.")
        lookup={}
        for sample in samples:
            canonical=f'{sample["series"]} №{sample["number"]}'
            for key in {canonical,f'{sample["series"]} {sample["number"]}',f'{sample["series"]}{sample["number"]}'}:
                lookup.setdefault(key.strip(),{})[sample["id"]]={**sample,"code":canonical}
        entries=[]
        for ri,row in enumerate(ROWS,8):
            for column in range(1,13):
                ci=column+1;address=f"{row}{column}"
                raw=main.cell(ri,ci).value
                numeric=isinstance(raw,(int,float,Decimal)) and not isinstance(raw,bool)
                if numeric:
                    numeric_value=Decimal(str(raw))
                    if not numeric_value.is_finite():raise ExcelError(address+": некорректная числовая подпись.")
                    raw=format(numeric_value,"f")
                if raw is not None and not isinstance(raw,str):raise ExcelError(address+": дата или логическое значение не является обозначением пробы; введите текст.")
                text=raw or ""
                dna_value=book["ДНК, мкл"].cell(ri,ci).value if "ДНК, мкл" in book else None
                water_value=book["Вода, мкл"].cell(ri,ci).value if "Вода, мкл" in book else None
                note_value=book["Примечания"].cell(ri,ci).value if "Примечания" in book else None
                hint=book["Типы"].cell(ri,ci).value if "Типы" in book else None
                if hint not in (None,"","sample","water","unregistered"):raise ExcelError(address+": неизвестный тип позиции.")
                if not text.strip():
                    if any(v is not None and v!="" for v in (dna_value,water_value,note_value,hint)):
                        raise ExcelError(address+": данные или тип указаны без обозначения позиции.")
                    entries.append({"row":row,"column":column,"empty":True});continue
                note=value_text(note_value)
                if len(note)>32767:raise ExcelError(address+": слишком длинное примечание.")
                kind=hint
                if not kind:kind="unregistered" if numeric else ("water" if text.strip().casefold()=="вода" else "sample")
                sample=None
                if kind=="sample":
                    matched=lookup.get(text.strip(),{})
                    if len(matched)>1:raise ExcelError(address+": обозначение неоднозначно соответствует нескольким пробам.")
                    sample=next(iter(matched.values()),None)
                    if sample is None:kind="unregistered"
                if kind=="water" and text.strip().casefold()!="вода":raise ExcelError(address+": тип контроля не согласован с надписью «вода».")
                if kind=="unregistered" and len(text)>500:raise ExcelError(address+": подпись длиннее 500 символов.")
                dna=quantity(dna_value,address+" / ДНК")
                water=quantity(water_value,address+" / вода")
                if kind=="water":
                    if dna is not None and Decimal(dna)!=0:raise ExcelError(address+": водный контроль должен содержать 0 мкл ДНК.")
                    dna="0"
                else:
                    if dna is None:dna=common_dna
                    if water is None:water=common_water
                entries.append({"row":row,"column":column,"kind":kind,"sample_id":sample["id"] if sample else None,
                    "sample_code":sample["code"] if sample else None,"label":text if kind=="unregistered" else "",
                    "dna":dna,"water":water,"note":note})
        return {"entries":entries,"metadata":{"source_name":value_text(main["B1"].value),"source_date":value_text(main["B2"].value),
            "source_actor":value_text(main["B3"].value),"common_dna":common_dna,"common_water":common_water,"notes_supplied":"Примечания" in book,"water_supplied":"Вода, мкл" in book}}
    finally:book.close()

def text_cell(cell,value):
    if len(value)>32767:raise ExcelError("Текст не помещается в ячейку Excel без потери данных.")
    cell.value=value;cell.data_type="s";cell.number_format="@"

def make_grid(book,name,metadata):
    sheet=book.create_sheet(name)
    labels=("Название блока","Дата создания","Исполнитель","ДНК, мкл","Вода, мкл")
    for r,label in enumerate(labels,1):sheet.cell(r,1,label)
    sheet.merge_cells("B1:M1");text_cell(sheet["B1"],metadata.get("name",""))
    text_cell(sheet["B2"],metadata.get("date",""));text_cell(sheet["B3"],metadata.get("actor",""))
    for key,field in (("B4","dna"),("B5","water")):
        value=metadata.get(field)
        if value is not None:text_cell(sheet[key],str(value))
    for col in range(1,13):sheet.cell(7,col+1,col)
    for i,row in enumerate(ROWS,8):sheet.cell(i,1,row)
    thin=Side(style="thin",color="A0A0A0")
    for row in sheet.iter_rows(min_row=7,max_row=15,min_col=1,max_col=13):
        for cell in row:
            cell.border=Border(left=thin,right=thin,top=thin,bottom=thin)
            cell.alignment=Alignment(horizontal="center",vertical="center",wrap_text=True)
            if cell.row>=8 and cell.column>=2:cell.number_format="@"
    sheet.column_dimensions["A"].width=23
    for column in range(2,14):sheet.column_dimensions[sheet.cell(7,column).column_letter].width=14
    for r in range(8,16):sheet.row_dimensions[r].height=42
    sheet.freeze_panes="B8";sheet.print_area="A1:M15";sheet.page_setup.orientation="landscape"
    sheet.page_setup.paperSize=sheet.PAPERSIZE_A4;sheet.page_setup.fitToWidth=1;sheet.page_setup.fitToHeight=1
    sheet.sheet_properties.pageSetUpPr.fitToPage=True
    return sheet

def export(metadata,entries,template=False):
    book=Workbook();book.remove(book.active);main=make_grid(book,"Исходный блок",metadata)
    if not template:
        for name in OPTIONAL:make_grid(book,name,metadata)
        for entry in entries:
            row=ROWS.index(entry["row"])+8;col=entry["column"]+1
            kind=entry["kind"]
            label=entry["sample_code"] if kind=="sample" else ("вода" if kind=="water" else entry["label"])
            text_cell(main.cell(row,col),label)
            if kind=="unregistered":
                main.cell(row,col).comment=Comment("Не зарегистрировано. Карточка пробы не создана.","LabCRM")
                main.cell(row,col).fill=PatternFill("solid",fgColor="FFF2CC")
            elif kind=="water":main.cell(row,col).fill=PatternFill("solid",fgColor="DDEBF7")
            for name,key in (("ДНК, мкл","dna"),("Вода, мкл","water"),("Примечания","note")):
                value=entry[key]
                if value is not None:text_cell(book[name].cell(row,col),str(value))
            text_cell(book["Типы"].cell(row,col),kind)
        book["Типы"].sheet_state="hidden"
    meta=book.create_sheet("_CRM");meta.append(["format","LABCRM_8G_1"])
    meta.append(["block",str(metadata.get("id",""))]);meta.append(["revision",str(metadata.get("revision",""))]);meta.sheet_state="hidden"
    output=BytesIO();book.save(output);book.close();return output.getvalue()
