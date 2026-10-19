import copy
from datetime import datetime
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
from zoneinfo import ZoneInfo
from automacao import State, Conflict, InvalidExtraction, due, make_plan, merge_desires, parse_extraction, process_file, recover, retry, lock
from connector_bridge import execute
from gmail_source import document_text, doc_ids
D = dict(faixa='A', categoria='Financeiro', dor='Falta de gestão financeira', desejo='Organizar recursos', dor_nova=False)
class Sheets:
    def __init__(self):
        self.data={('a','Dores'):[['Financeiro',D['dor'],D['desejo'],4,'Autor','Método','CTA','Coberta']],('a','Revisar'):[],('b','Dores'):[],('b','Revisar'):[]}; self.calls=0; self.crash=False
    def rows(self,sid,tab):return copy.deepcopy(self.data[(sid,tab)])
    def row(self,sid,tab,n):
        rows=self.data[(sid,tab)];return copy.deepcopy(rows[n-2]) if n-2<len(rows) else []
    def apply(self,p):
        self.calls+=1;rows=self.data[(p['spreadsheet'],p['tab'])]
        while len(rows)<p['row']-1:rows.append(['']*8)
        for c in p['columns']:rows[p['row']-2][c]=p['after'][c]
        if self.crash:raise TimeoutError()
class Tests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.path=Path(t.name)/'state.json';self.state=State(self.path);self.sh=Sheets()
    def plan(self,d=None,tab='Dores'):return make_plan('call1',d or D,'a',tab,self.sh.rows('a',tab))
    def test_accents_case_spaces(self):
        p=self.plan(dict(D,dor=' FALTA de GESTAO  FINANCEIRA ',desejo='ORGANIZAR RECURSOS'))
        self.assertEqual(p['row'],2);self.assertEqual(p['after'][2:],[D['desejo'],5,'Autor','Método','CTA','Coberta'])
    def test_two_runs(self):
        src=Mock();ex=Mock();ex.classify.return_value='A';ex.extract.return_value=D
        args=({'id':'call1'},src,self.sh,ex,self.state,{'A':'a','B':'b'})
        self.assertTrue(process_file(*args));self.assertFalse(process_file(*args));self.assertEqual(self.sh.row('a','Dores',2)[3],5)
    def test_crash_after_commit(self):
        self.state.prepare(self.plan());self.sh.crash=True
        with self.assertRaises(TimeoutError):recover(self.state,self.sh)
        self.sh.crash=False;recover(State(self.path),self.sh)
        self.assertEqual(self.sh.calls,1);self.assertTrue(State(self.path).done('call1'))
    def test_crash_before_commit(self):
        self.state.prepare(self.plan());recover(State(self.path),self.sh);self.assertEqual(self.sh.calls,1)
    def test_never_decrease_manual_score(self):
        self.state.prepare(self.plan());self.sh.data[('a','Dores')][0][3]=9
        with self.assertRaises(Conflict):recover(self.state,self.sh)
        self.assertEqual(self.sh.row('a','Dores',2)[3],9);self.assertEqual(self.sh.calls,0)
    def test_editorial_fields_preserved(self):
        self.state.prepare(self.plan());self.sh.data[('a','Dores')][0][7]='Em produção';recover(self.state,self.sh)
        self.assertEqual(self.sh.row('a','Dores',2)[7],'Em produção')
    def test_append_after_blank_rows(self):
        self.sh.data[('a','Dores')]+=[[],['Gestão','Outra dor','',8,'','','','Ativa']]
        p=self.plan(dict(D,dor='Equipe sem autonomia'));self.assertEqual(p['row'],5)
        before=self.sh.rows('a','Dores');self.state.prepare(p);recover(self.state,self.sh)
        self.assertEqual(self.sh.rows('a','Dores')[:3],before)
    def test_review_aggregation(self):
        self.sh.data[('a','Revisar')]=[['Financeiro',D['dor'],'',1,'','','','Ativa']]
        self.assertEqual(self.plan(dict(D,dor_nova=True),'Revisar')['after'][3],2)
    def test_routing(self):
        for f,new,sid,tab in [('A',True,'a','Revisar'),('B',True,'b','Revisar'),('B',False,'b','Dores'),('nao_informado',False,'a','Dores')]:
            st=State(self.path.with_name(f+str(new)+'.json'));src=Mock();ex=Mock();ex.classify.return_value=f;ex.extract.return_value=dict(D,faixa=f,dor_nova=new)
            process_file({'id':'call'},src,self.sh,ex,st,{'A':'a','B':'b'})
            self.assertEqual(st.data['processed']['call']['tab'],tab);self.assertEqual(st.data['processed']['call']['spreadsheet'],sid)
    def test_exclude_onboarding(self):
        src=Mock();ex=Mock();ex.is_commercial.return_value=False
        self.assertFalse(process_file({'id':'alan'},src,self.sh,ex,self.state,{'A':'a','B':'b'}));ex.extract.assert_not_called()
    def test_conflicting_faixa(self):
        src=Mock();ex=Mock();ex.classify.return_value='B';ex.extract.return_value=D
        with self.assertRaises(InvalidExtraction):process_file({'id':'call'},src,self.sh,ex,self.state,{'A':'a','B':'b'})
        self.assertEqual(self.sh.calls,0)
    def test_corrupt_state(self):
        self.path.write_text('{')
        with self.assertRaises(ValueError):State(self.path)
        self.assertEqual(self.path.read_text(),'{')
    def test_invalid_score(self):
        for s in ['',-1,'=1+2',1.5,True]:
            self.sh.data[('a','Dores')][0][3]=s
            with self.assertRaises(Conflict):self.plan()
    def test_duplicate_rows(self):
        self.sh.data[('a','Dores')]*=2
        with self.assertRaises(Conflict):self.plan()
    def test_bad_json(self):
        for s in ['{','[]',json.dumps(dict(D,dor_nova='true')),json.dumps(dict(D,faixa='C'))]:
            with self.assertRaises(InvalidExtraction):parse_extraction(s)
    def test_desires(self):self.assertEqual(merge_desires('Mais lucro; Equipe autônoma','EQUIPE AUTONOMA; Mais tempo'),'Mais lucro; Equipe autônoma; Mais tempo')
    def test_retries(self):
        op=Mock(side_effect=[TimeoutError(),TimeoutError(),'ok']);sleep=Mock();self.assertEqual(retry(op,sleep),'ok');self.assertEqual([x.args[0] for x in sleep.call_args_list],[2,4])
    def test_monday_seven(self):
        z=ZoneInfo('America/Sao_Paulo')
        for day,h,m,want in [(21,6,59,False),(21,7,0,True),(22,7,0,False)]:self.assertEqual(due(datetime(2026,9,day,h,m,tzinfo=z),'07:00',None),want)
        self.assertFalse(due(datetime(2026,9,21,8,tzinfo=z),'07:00','2026-09-21'))
    def test_lock(self):
        p=str(self.path)+'.lock'
        with lock(p):
            with self.assertRaises(Conflict):
                with lock(p):pass
    def test_second_doc_tab(self):
        d={'title':'Anotações','tabs':[{'tabProperties':{'title':'Observações'},'documentTab':{'body':{'textRun':{'content':'resumo'}}}},{'tabProperties':{'title':'Transcrição'},'documentTab':{'body':{'textRun':{'content':'fala completa'}}}}]}
        self.assertEqual(document_text(d),'fala completa')
        with self.assertRaises(InvalidExtraction):document_text({'title':'Chat','body':{'textRun':{'content':'bot'}}})
    def test_urls(self):self.assertEqual(doc_ids({'body':{'content':'https://docs.google.com/document/d/abc/edit https://evil.test/abc https://docs.google.com/document/d/abc/edit?tab=1'}}),['abc'])
    def test_bridge(self):
        p={'file_id':'c','commercial_verified':True,'extraction':D,'ids':{'A':'a','B':'b'},'tab':'Dores','spreadsheet':'a','sheet_id':123,'rows':self.sh.rows('a','Dores')}
        execute('prepare',p,self.state);r={'spreadsheet':'a','tab':'Dores','row':2,'values':self.sh.row('a','Dores',2)}
        self.assertEqual(len(execute('reconcile',r,self.state)['requests']),2);r['values'][3]=5
        self.assertEqual(execute('confirm',r,self.state)['status'],'confirmed');self.assertEqual(execute('prepare',p,self.state)['status'],'already_processed')
if __name__=='__main__':unittest.main()
