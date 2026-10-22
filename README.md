# TeleAgent-Cost-Statistician

TeleAgentר�õ�ִ�й���ͳ��Skill����ȡ�Ѿ����й��Ĳ���������־�����ɾ������ָ��Excel������������ɼ�����Word���Լ����ػز�JSON������ÿ�ζ������ԡ�׷�Ӽ���ִ�жΡ���Ŀ��ֵ�Ͷ����ֵ��

## ʹ��

�� [skills/teleagent-cost-statistician](skills/teleagent-cost-statistician) �ļ��и��Ƶ�����Agent�ļ���Ŀ¼��Codex��ʹ�� `$CODEX_HOME/skills`��δ����ʱʹ�� `~/.codex/skills`��Ҳ��ֱ���Ķ� [SKILL.md](skills/teleagent-cost-statistician/SKILL.md) �����нű������谲װ������Python�⣬Ҫ��Python 3.10�����ϰ汾��

```powershell
python skills/teleagent-cost-statistician/scripts/collect.py --tasks "��Ŀ��Ŀ¼" --workspace "����Ŀ¼�����θ�Ŀ¼" --db "�˺����ݿ�·��" --output "���ؽ��Ŀ¼"
```

`--workspace`��`--session`֧���ظ����룻`--date`������ȷҪ�޶�����ʱʹ�ã�Ĭ��ʱ��Asia/Shanghai��SQLite������ֻ�������ȡ��JSONL���������޸�Դ�ļ���ÿ�βɼ���ȡ���飬�����ݱ仯���ֶ�У��ʧ�ܼ�ֹͣ��

## �����ͳ�ƿھ�

ָ��Ϊ��ʱ��LLM��ɵ�����������Token�����Token�����������ʡ����ߵ��á����ߴ����������á������ļ�������ʱ��ʾʱ���룬Token����Ϊ��λ��LLM��ɵ�����������ʧ����������ģ�������пɿ��ֶ�ʱ���С�

��������ͬ�����²��ԣ�����ָ����ԭ���Ժ����Ρ�ȱʧ�����㣬��������ֵ��ʧ��ִ�в����ֵ������ֵ����Ŀ��Ȩ�������Դ���ƽ�����С���� [��־�ֶ�](skills/teleagent-cost-statistician/references/log-schema.md) �� [����ھ�](skills/teleagent-cost-statistician/references/output-contract.md)��

��ǰ��������ҽ�Token�뻺�����ݹ������ɱ�������û�е��ۺͿɿ��˵�ʱ�����ɷ��á�ҳ��֤�ݿ�ͨ��`--observations`������Դ��Agent�Ķ�JSON�����`report.py --analysis`����Word�е��оݷ�����

## ��֤

```powershell
python -m unittest discover -s tests -v
```

��������ʱĿ¼���ɺϳ���־�������ظ�������������������������ȥ�ء��ڲ����ߴ���ȱʧ���㡢��Ŀ��Ȩ��ֵ�������λ���ļ��ṹ���ֿⲻ������ʵ������Ŀ��ԭʼ��־��������н����
