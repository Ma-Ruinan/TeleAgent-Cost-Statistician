import unittest
import zipfile
import xml.etree.ElementTree as ET
import test_agent_tree as fixtures
from common import task_source
import report


class DatasetTests(unittest.TestCase):
    def test_source_normalization(self):
        self.assertEqual(task_source({'dimension':'常规能力'}),'综合能力')
        self.assertEqual(task_source({'source':'E:/综合测试数据集/alphadata测试数据集/维度一/问题描述.txt'}),'AlphaData')
        self.assertEqual(task_source({'dimension':'deepinsight'}),'DeepInsight')
        self.assertEqual(task_source({'name':'AI Agent research'}),'未识别')

    def test_export_columns_and_formulas_move_together(self):
        case=fixtures.AgentTreeTests();case.setUp()
        try:
            case.tasks[0]['dimension']='alphadata'
            data=case.collect(case.fixture());original=data['runs'][0]['values'].copy()
            data['meta']=dict(timezone='Asia/Shanghai',method='test',source='test',collected_at='test',read_twice_equal=True,snapshot_sha256='test')
            path=case.root/'result.xlsx';report.excel_report(data,path)
            self.assertEqual(data['runs'][0]['dataset_source'],'AlphaData')
            self.assertEqual(data['runs'][0]['values'],original)
            ns={'s':report.S}
            with zipfile.ZipFile(path) as z:
                sheets=[ET.fromstring(z.read(f'xl/worksheets/sheet{i}.xml')) for i in range(1,5)]
            def cell(sheet,ref):return sheet.find(f'.//s:c[@r="{ref}"]',ns)
            def text(c):return ''.join(c.itertext())
            self.assertEqual(text(cell(sheets[0],'A6')),'题目来源')
            self.assertEqual(text(cell(sheets[0],'A7')),'AlphaData')
            self.assertEqual(text(cell(sheets[0],'B7')),'Q1')
            count=cell(sheets[0],'D7').find('s:f',ns).text
            self.assertIn("'逐次记录'!$C$2",count);self.assertIn('B7',count)
            duration=cell(sheets[0],'E7').find('s:f',ns).text
            self.assertIn("'逐次记录'!$H$2",duration)
            for sheet in sheets[1:]:
                self.assertEqual(text(cell(sheet,'B1')),'题目来源')
                self.assertEqual(text(cell(sheet,'B2')),'AlphaData')
                self.assertEqual(text(cell(sheet,'C2')),'Q1')
        finally:case.tearDown()
