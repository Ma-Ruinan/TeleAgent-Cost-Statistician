import unittest
import zipfile
import xml.etree.ElementTree as ET
import test_agent_tree as fixtures
from common import task_source, task_identity, DataError
import report


class DatasetTests(unittest.TestCase):
    def test_display_identity_uses_original_folder_not_agent_labels(self):
        for label in ('AD1-5','AD-5','AD-1-5'):
            task=dict(id=label,name='错误的扩展标题（说明）',source='E:/AlphaData/维度一-基础处理/5_特征工程与分组均值/问题描述.txt')
            self.assertEqual(task_identity(task,True),('1-5','特征工程与分组均值'))
        for title in ('实时信息抓取与整理(Anthropic动态)','实时信息抓取与整理（Anthropic 重要动态）','实时信息抓取与整理'):
            self.assertEqual(task_identity(dict(id='W4',name=title,source='E:/常规能力/维度1_信息检索与浏览/W4_实时信息抓取与整理/问题描述.txt'),True),('1-4','实时信息抓取与整理'))

    def test_cannot_guess_missing_dimension_from_ad5(self):
        with self.assertRaises(DataError):task_identity(dict(id='AD-5',name='特征工程与分组均值'),True)
        self.assertEqual(task_identity(dict(source='E:/AlphaData/维度十二-示例/05_原题（原目录括号）/question.txt'),True),('12-5','原题（原目录括号）'))

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
            self.assertEqual(text(cell(sheets[0],'B7')),'1-1')
            count=cell(sheets[0],'D7').find('s:f',ns).text
            self.assertIn("'逐次记录'!$C$2",count);self.assertIn('B7',count)
            self.assertIn("'逐次记录'!$B$2",count);self.assertIn('A7',count)
            duration=cell(sheets[0],'E7').find('s:f',ns).text
            self.assertIn("'逐次记录'!$H$2",duration)
            for sheet in sheets[1:]:
                self.assertEqual(text(cell(sheet,'B1')),'题目来源')
                self.assertEqual(text(cell(sheet,'B2')),'AlphaData')
                self.assertEqual(text(cell(sheet,'C2')),'1-1')
        finally:case.tearDown()
