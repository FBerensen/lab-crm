import unittest
from io import BytesIO
from zipfile import ZipFile,ZIP_DEFLATED
from openpyxl import load_workbook
from lab.source_blocks8g_excel import export,parse,ExcelError,checked_book,quantity

META={"id":1,"revision":0,"name":"Тест","date":"2026-11-06","actor":"tester","dna":"10","water":"160"}
SAMPLES=[{"id":1,"series":"QQ","number":"1"}]

def edited(changes,initial=None):
    b=load_workbook(BytesIO(initial or export(META,[],True)))
    for sheet,cell,value in changes:b[sheet][cell]=value
    out=BytesIO();b.save(out);b.close();return out.getvalue()

def first(data,samples=SAMPLES):return parse(data,"10","160",samples)["entries"][0]

def record(kind="sample",**kwargs):
    return {"row":"A","column":1,"kind":kind,"sample_id":1 if kind=="sample" else None,
        "sample_code":"QQ №1" if kind=="sample" else None,"label":"старый-821" if kind=="unregistered" else "",
        "dna":"0" if kind=="water" else "1.25","water":None if kind=="water" else "2.5","note":"test",**kwargs}

class Excel8GCodecTests(unittest.TestCase):
    def test_empty_template_has_96_empty_positions(self):
        entries=parse(export(META,[],True),10,160,SAMPLES)["entries"]
        self.assertEqual(len(entries),96);self.assertTrue(all(e.get("empty") for e in entries))
    def test_main_coordinates(self):
        b=checked_book(export(META,[],True));s=b.worksheets[0]
        self.assertEqual(s["B7"].value,1);self.assertEqual(s["M7"].value,12);self.assertEqual(s["A8"].value,"A");self.assertEqual(s["A15"].value,"H");b.close()
    def test_canonical_match(self):self.assertEqual(first(edited([("Исходный блок","B8","QQ №1")]))["sample_id"],1)
    def test_spaced_match(self):self.assertEqual(first(edited([("Исходный блок","B8","QQ 1")]))["kind"],"sample")
    def test_compact_match(self):self.assertEqual(first(edited([("Исходный блок","B8","QQ1")]))["sample_id"],1)
    def test_unknown_becomes_raw(self):
        e=first(edited([("Исходный блок","B8","к-821")]))
        self.assertEqual((e["kind"],e["label"],e["sample_id"]),("unregistered","к-821",None))
    def test_water_has_no_automatic_water_volume(self):
        e=first(edited([("Исходный блок","B8","вода")]))
        self.assertEqual((e["kind"],e["dna"],e["water"]),("water","0",None))
    def test_water_case_and_spaces(self):self.assertEqual(first(edited([("Исходный блок","B8"," ВОДА ")]))["kind"],"water")
    def test_defaults_from_header(self):
        e=first(edited([("Исходный блок","B8","QQ 1"),("Исходный блок","B4",3),("Исходный блок","B5",7)]))
        self.assertEqual((e["dna"],e["water"]),("3","7"))
    def test_blank_header_falls_back(self):
        data=edited([("Исходный блок","B8","QQ 1"),("Исходный блок","B4",None),("Исходный блок","B5",None)])
        self.assertEqual(first(data)["water"],"160")
    def test_roundtrip_registered(self):
        e=record();got=first(export(META,[e]));self.assertEqual(got,e)
    def test_roundtrip_raw(self):
        e=record("unregistered");self.assertEqual(first(export(META,[e])),e)
    def test_roundtrip_water_unknown(self):
        e=record("water");self.assertEqual(first(export(META,[e])),e)
    def test_roundtrip_water_known(self):
        e=record("water",water="0");self.assertEqual(first(export(META,[e])),e)
    def test_raw_equal_to_registered_label_stays_raw(self):
        e=record("unregistered",label="QQ №1");self.assertEqual(first(export(META,[e]))["kind"],"unregistered")
    def test_raw_water_label_stays_raw(self):
        e=record("unregistered",label="вода");self.assertEqual(first(export(META,[e]))["kind"],"unregistered")
    def test_replicates(self):
        a=record();b=record(column=12,row="H")
        parsed=parse(export(META,[a,b]),10,160,SAMPLES)["entries"]
        self.assertEqual(parsed[0]["sample_id"],parsed[95]["sample_id"])
    def test_formula_rejected(self):
        with self.assertRaises(ExcelError):first(edited([("Исходный блок","B8","=1+1")]))
    def test_export_formula_like_raw_forced_to_text(self):
        e=record("unregistered",label="=SUM(1,2)")
        self.assertEqual(first(export(META,[e]))["label"],"=SUM(1,2)")
    def test_export_formula_like_note_forced_to_text(self):
        e=record(note="=cmd()")
        self.assertEqual(first(export(META,[e]))["note"],"=cmd()")
    def test_error_cell_rejected(self):
        with self.assertRaises(ExcelError):first(edited([("Исходный блок","B8","#N/A")]))
    def test_negative_rejected(self):
        with self.assertRaises(ExcelError):quantity(-1,"A1")
    def test_nonfinite_rejected(self):
        for v in ("NaN","Infinity","-Infinity"):
            with self.assertRaises(ExcelError):quantity(v,"A1")
    def test_precision_rejected(self):
        with self.assertRaises(ExcelError):quantity("0.00001","A1")
        self.assertEqual(quantity("1.25000","A1"),"1.25")
    def test_comma_decimal_accepted(self):self.assertEqual(quantity("1,25","A1"),"1.25")
    def test_explicit_zero(self):self.assertEqual(quantity(0,"A1"),"0")
    def test_boolean_rejected(self):
        with self.assertRaises(ExcelError):quantity(True,"A1")
    def test_bad_headers_rejected(self):
        with self.assertRaises(ExcelError):first(edited([("Исходный блок","B7",2)]))
        with self.assertRaises(ExcelError):first(edited([("Исходный блок","B16","QQ 1")]))
    def test_numeric_identity_is_unregistered(self):
        e=first(edited([("Исходный блок","B8",821)]))
        self.assertEqual((e["kind"],e["label"]),("unregistered","821"))
    def test_date_identity_rejected(self):
        from datetime import datetime
        with self.assertRaises(ExcelError):first(edited([("Исходный блок","B8",datetime(2020,1,2))]))
    def test_ambiguous_alias_rejected(self):
        samples=[{"id":1,"series":"AB","number":"12"},{"id":2,"series":"AB1","number":"2"}]
        with self.assertRaises(ExcelError):first(edited([("Исходный блок","B8","AB12")]),samples)
    def test_volume_without_identity_rejected(self):
        data=export(META,[])
        with self.assertRaises(ExcelError):first(edited([("Вода, мкл","B8",4)],data))
    def test_control_nonzero_dna_rejected(self):
        with self.assertRaises(ExcelError):first(export(META,[record("water",dna="1")]))
    def test_oversized_file_rejected(self):
        with self.assertRaises(ExcelError):checked_book(b"x"*(2*1024*1024+1))
    def test_invalid_zip_rejected(self):
        with self.assertRaises(ExcelError):checked_book(b"not a workbook")
    def test_merged_data_cells_rejected(self):
        b=load_workbook(BytesIO(export(META,[],True)));b.active.merge_cells("B8:C8");out=BytesIO();b.save(out)
        with self.assertRaises(ExcelError):first(out.getvalue())
    def test_external_link_rejected(self):
        src=export(META,[],True);output=BytesIO()
        with ZipFile(BytesIO(src)) as a,ZipFile(output,"w",ZIP_DEFLATED) as z:
            for name in a.namelist():z.writestr(name,a.read(name))
            z.writestr('xl/_rels/fake.xml.rels','<Relationships><Relationship TargetMode="External" Target="https://example.invalid"/></Relationships>')
        with self.assertRaises(ExcelError):checked_book(output.getvalue())
