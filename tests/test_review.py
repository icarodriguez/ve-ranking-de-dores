import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
from automacao import State, Conflict, Extractor
from connector_bridge import execute

class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = State(Path(self.tmp.name) / 'state.json')
        self.data = dict(faixa='A', categoria='Gestão', dor='Gestão dependente dos donos',
                         desejo='Delegar tarefas', dor_nova=True)
        self.payload = dict(file_id='doc', source_type='hotseat', eligible_verified=True,
                            reviewed=True, evidence='Participante relata sua operação; receita de 100 mil.',
                            extraction=self.data, ids={'A':'a','B':'b'}, spreadsheet='a',
                            tab='Dores', sheet_id=1, rows=[])

    def test_canonical_id_across_modes(self):
        self.state.data['processed']['doc'] = {}
        self.assertTrue(self.state.done('drive:doc'))
        self.state.data['processed'] = {'drive:other':{}}
        self.assertTrue(self.state.done('other'))

    def test_unknown_revenue_enters_a_after_review(self):
        self.payload['extraction']['faixa'] = 'nao_informado'
        result = execute('prepare', self.payload, self.state)
        self.assertEqual(result['tab'], 'Dores')
        self.assertEqual(result['spreadsheet'], 'a')
        self.assertEqual(self.state.data['pending']['extraction']['faixa'], 'nao_informado')

    def test_unknown_new_pain_still_requires_review(self):
        self.payload['extraction']['faixa'] = 'nao_informado'
        self.payload.update(reviewed=False, tab='Revisar')
        self.assertEqual(execute('prepare', self.payload, self.state)['tab'], 'Revisar')

    def test_review_requires_evidence(self):
        self.payload['evidence'] = ''
        with self.assertRaises(Conflict):
            execute('prepare', self.payload, self.state)

    def test_promotion_is_idempotent_and_keeps_receipt(self):
        original = dict(tab='Revisar', row=2, extraction=self.data, spreadsheet='a')
        self.state.data['processed']['doc'] = original
        execute('review', self.payload, self.state)
        read = dict(spreadsheet='a', tab='Dores', row=2, values=[])
        self.assertEqual(execute('reconcile', read, self.state)['status'], 'write_required')
        read['values'] = self.state.data['pending']['after']
        execute('confirm', read, self.state)
        self.assertEqual(self.state.data['processed']['doc']['previous_receipt'], original)
        self.assertEqual(execute('review', self.payload, self.state)['status'], 'already_reviewed')
        self.assertEqual(len(self.state.data['processed']), 1)

    def test_hotseat_selection_is_tied_to_same_participant(self):
        ex = Extractor(Mock(), 'test-model')
        ex.ask = Mock(side_effect=[
            json.dumps(dict(elegivel=True, motivo='Hotseat com relatos', tipo='hotseat')),
            json.dumps(dict(participante='Eloisa', faixa='nao_informado')),
            json.dumps(dict(self.data, faixa='nao_informado'))])
        self.assertTrue(ex.is_commercial('fala', 'Hotseat com Alan'))
        self.assertEqual(ex.classify('fala'), 'nao_informado')
        ex.extract('fala', [])
        self.assertIn('participante Eloisa', ex.ask.call_args.args[0])
        self.assertIn('Alan', ex.ask.call_args.args[0])

    def test_api_commercial_prompt_unchanged(self):
        from automacao import BASE
        ex = Extractor(Mock(), 'test-model')
        ex.ask = Mock(return_value=json.dumps(self.data))
        ex.extract('texto', [])
        expected = (BASE / 'prompt_extracao.txt').read_text().rstrip('\n')
        expected = expected.replace('{{lista_de_dores}}', '[]').replace('{{transcricao}}', 'texto')
        self.assertEqual(ex.ask.call_args.args[0], expected)

if __name__ == '__main__':
    unittest.main()
