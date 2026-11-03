from datetime import datetime, timedelta, timezone
from decimal import Decimal
import csv
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from types import SimpleNamespace

import numpy as np
import pandas as pd

from headline_model.engine import HeadlineEngine, Journal, Position
from headline_model.execution import PaperBroker, PositionState, execute
from headline_model.main import consume_queue, extract, prepare
from headline_model.muse_matcher import MetaClient, MuseMatcher, validate_assessment
from headline_model.ridge_model import FEATURES, PROMPT_VERSION, PositionRidge
from headline_model.update_receiver import UpdateQueue, validate_update

NOW = datetime(2026,10,26,2,0,tzinfo=timezone.utc)


def position(**changes):
    d = dict(id='merger-yes',platform='kalshi',market_id='EXAMPLE',outcome='YES',question='Will Acme merger close before December?',thesis='The Acme merger will close.',resolution_rules='YES if the Acme merger legally closes before December 1, 2026.',deadline='2026-12-01T00:00:00Z',relevance_groups=[['Acme'],['merger','acquisition']],quantity='100',mark_price='0.5',min_order_size='1',quantity_step='1',allow_partial=True,tradable=True,has_open_orders=False)
    d.update(changes)
    return d


def article(**changes):
    d=dict(id='fixture-1',headline='Acme merger blocked by regulator',published_at=NOW.isoformat())
    d.update(changes)
    return d


def portfolio(**changes):
    d=dict(basket_id='fixture-basket',as_of=NOW.isoformat(),nav='100',positions=[position()])
    d.update(changes)
    return d


def evidence(**changes):
    d=dict(related=True,confidence=.95,reason='The exact merger is blocked.',evidence=['Acme merger blocked by regulator'],adverse_evidence=.95,favorable_evidence=.02,uncertainty=.05,event_specificity=.95,time_alignment=.95)
    d.update(changes)
    return d


class FixtureMatcher:
    def __init__(self,data=None,error=None):
        self.data,self.error,self.calls=data or evidence(),error,0
    def candidates(self,news,positions):
        return {p.id for p in positions}
    def assess(self,news,p):
        self.calls+=1
        if self.error:
            raise ValueError(self.error)
        return validate_assessment(self.data,news,'fixture-model')


class FixtureScorer:
    def __init__(self,score=-.9):
        self.value=score
    def score(self,assessment):
        return self.value


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.path=Path(self.temp.name)/'journal.sqlite'
        self.journal=Journal(self.path)
        self.matcher=FixtureMatcher()
        self.engine=HeadlineEngine(FixtureScorer(),self.journal,'.5',matcher=self.matcher)
    def tearDown(self):
        self.journal.close()
        self.temp.cleanup()
    def decide(self,news=None,book=None):
        return self.engine.process(news or article(),book or portfolio(),now=NOW)[0]
    def test_negative_related_reduces(self):
        d=self.decide()
        self.assertEqual((d['action'],d['intent']['quantity'],d['intent']['estimated_weight_after']),('REDUCE_WEIGHT','50','0.25'))
        self.assertEqual(d['execution'],'NOT_SUBMITTED')
    def test_exit_if_partial_not_supported(self):
        d=self.decide(book=portfolio(positions=[position(allow_partial=False)]))
        self.assertEqual((d['action'],d['intent']['quantity']),('EXIT','100'))
    def test_exit_if_partial_below_minimum(self):
        self.assertEqual(self.decide(book=portfolio(positions=[position(quantity='1')]))['action'],'EXIT')
    def test_no_trade_below_exchange_minimum(self):
        self.assertEqual(self.decide(book=portfolio(positions=[position(quantity='1',min_order_size='2')]))['action'],'HOLD')
    def test_unrelated_negative_does_not_reduce(self):
        self.engine.matcher=FixtureMatcher(evidence(related=False,evidence=[]))
        self.assertEqual(self.decide()['reason'],'unrelated_headline')
    def test_favorable_no_position_is_held(self):
        self.engine.scorer=FixtureScorer(.9)
        self.assertEqual(self.decide(book=portfolio(positions=[position(outcome='NO')]))['reason'],'not_negative_for_held_bet')
    def test_sell_preserves_held_no_outcome(self):
        self.assertEqual(self.decide(book=portfolio(positions=[position(outcome='NO')]))['outcome'],'NO')
    def test_duplicate_skips_api(self):
        self.decide()
        self.assertEqual(self.decide()['reason'],'duplicate_headline')
        self.assertEqual(self.matcher.calls,1)
    def test_dedup_survives_restart(self):
        self.decide()
        self.journal.close()
        self.journal=Journal(self.path)
        self.engine.journal=self.journal
        self.assertEqual(self.decide()['reason'],'duplicate_headline')
    def test_cooldown_blocks_repeated_reduction(self):
        self.decide()
        news=article(headline='Acme merger blocked by regulator again')
        self.engine.matcher=FixtureMatcher(evidence(evidence=[news['headline']]))
        self.assertEqual(self.decide(news=news)['reason'],'position_cooldown')
    def test_missing_model_and_verifier(self):
        self.engine.scorer=None
        self.assertEqual(self.decide()['reason'],'no_trained_model')
        self.engine.scorer=FixtureScorer()
        self.engine.matcher=None
        self.assertEqual(self.decide()['reason'],'no_muse_relevance_verifier')
    def test_uncertainty_abstains(self):
        self.engine.matcher=FixtureMatcher(evidence(uncertainty=.9))
        self.assertEqual(self.decide()['reason'],'uncertain_or_weak_relevance')
    def test_provider_failure_abstains(self):
        self.engine.matcher=FixtureMatcher(error='unavailable')
        self.assertEqual(self.decide()['reason'],'model_abstained')
    def test_stale_news_and_snapshot_abstain(self):
        self.assertEqual(self.decide(news=article(published_at=(NOW-timedelta(hours=3)).isoformat()))['reason'],'stale_or_future_headline')
        self.assertEqual(self.decide(book=portfolio(as_of=(NOW-timedelta(minutes=5)).isoformat()))['reason'],'stale_or_future_portfolio_snapshot')
    def test_expired_market_and_existing_orders_abstain(self):
        self.assertEqual(self.decide(book=portfolio(positions=[position(deadline=NOW.isoformat())]))['reason'],'past_resolution_deadline')
        self.assertEqual(self.decide(book=portfolio(positions=[position(has_open_orders=True)]))['reason'],'reconcile_existing_orders_first')
    def test_invalid_inputs_rejected(self):
        for p in [position(quantity='NaN'),position(mark_price='1.1'),position(quantity='1.5'),position(allow_partial='true')]:
            with self.assertRaises((ValueError,ArithmeticError)):
                self.decide(book=portfolio(positions=[p]))
        for fraction in ['0','1','NaN']:
            with self.assertRaises(ValueError):
                HeadlineEngine(None,self.journal,fraction)


class ApiTests(unittest.TestCase):
    def test_invalid_evidence_is_retried_without_accepting_invented_quotes(self):
        calls=[]
        def opener(request,timeout):
            calls.append(1)
            data=evidence(evidence=['invented quote']) if len(calls)==1 else evidence()
            return io.BytesIO(json.dumps({'status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':json.dumps(data)}]}]}).encode())
        result=MetaClient('fixture-key',opener=opener,sleeper=lambda _:None).assess(article(),Position.parse(position()))
        self.assertEqual(len(calls),2)
        self.assertEqual(result.evidence,[article()['headline']])

    def test_token_limit_retry_increases_allowance_within_three_attempts(self):
        calls=[]
        def opener(request,timeout):
            calls.append(json.loads(request.data)['max_output_tokens'])
            body={'status':'incomplete','incomplete_details':{'reason':'max_output_tokens'}} if len(calls)==1 else {'status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':json.dumps(evidence())}]}]}
            return io.BytesIO(json.dumps(body).encode())
        self.assertTrue(MetaClient('fixture-key',opener=opener,sleeper=lambda _:None).assess(article(),Position.parse(position())).related)
        self.assertEqual(calls,[4096,8192])

    def test_missing_key(self):
        with patch.dict('os.environ',{'MODEL_API_KEY':''}):
            with self.assertRaises(ValueError):
                MetaClient()
    def test_schema_request_and_response(self):
        calls=[]
        def opener(request,timeout):
            calls.append(request)
            return io.BytesIO(json.dumps({'status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':json.dumps(evidence())}]}]}).encode())
        self.assertTrue(MetaClient('fixture-key',opener=opener).assess(article(),Position.parse(position())).related)
        payload=json.loads(calls[0].data)
        self.assertFalse(payload['store'])
        self.assertIn('text',payload)
        data=json.loads(payload['input'])
        self.assertEqual(data['held_outcome'],'YES')
        self.assertNotIn('quantity',data)
    def test_auth_error_not_retried_or_exposed(self):
        calls=[]
        def opener(request,timeout):
            calls.append(1)
            raise HTTPError(request.full_url,401,'secret body',{},None)
        with self.assertRaisesRegex(ValueError,'HTTP 401') as caught:
            MetaClient('fixture-key',opener=opener).assess(article(),Position.parse(position()))
        self.assertNotIn('secret',str(caught.exception))
        self.assertEqual(len(calls),1)
    def test_temporary_failure_retries(self):
        calls=[]
        def opener(request,timeout):
            calls.append(1)
            if len(calls)<3:
                raise HTTPError(request.full_url,429,'limited',{},None)
            return io.BytesIO(json.dumps({'status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':json.dumps(evidence())}]}]}).encode())
        self.assertTrue(MetaClient('fixture-key',opener=opener,sleeper=lambda _:None).assess(article(),Position.parse(position())).related)
        self.assertEqual(len(calls),3)
    def test_invalid_evidence_rejected(self):
        for d in [evidence(evidence=['invented claim']),evidence(confidence=float('nan')),evidence(confidence=True),evidence(adverse_evidence=2),evidence(related='yes')]:
            with self.assertRaises(ValueError):
                validate_assessment(d,article(),'fixture')
    def test_candidate_ranking(self):
        positions=[Position.parse(position(id='other',question='Will it rain in London?',thesis='Rain',resolution_rules='London rainfall.',relevance_groups=[['London'],['rain']])),Position.parse(position())]
        self.assertEqual(MuseMatcher(None,top_k=1).candidates(article(),positions),{'merger-yes'})


class MuseUpdateTests(unittest.TestCase):
    def test_agent_update_preserves_source_time(self):
        incoming=article(url='https://example.com/story')
        result=validate_update(incoming,NOW)
        self.assertEqual(result['published_at'],NOW.isoformat())
        self.assertEqual(result['provider'],'Muse app/agent')
        self.assertNotIn('sentiment',result)
    def test_missing_or_unsafe_source_is_rejected(self):
        for link in ['', 'file:///etc/passwd', 'https://user:password@example.com/story']:
            with self.assertRaises(ValueError):
                validate_update(article(url=link),NOW)
    def test_stale_future_and_unknown_dates_are_rejected(self):
        for stamp in [(NOW-timedelta(hours=2)).isoformat(),(NOW+timedelta(hours=1)).isoformat(),'unknown','2026-10-26T02:00:00']:
            with self.assertRaises(ValueError):
                validate_update(article(url='https://example.com/story',published_at=stamp),NOW)
    def test_duplicate_agent_updates_are_not_requeued(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)
            update=validate_update(article(url='https://example.com/story'),NOW)
            queue=UpdateQueue(path/'state.sqlite')
            self.assertEqual(queue.append([update],path/'updates.jsonl'),1)
            self.assertEqual(queue.append([update],path/'updates.jsonl'),0)
            queue.close()
            queue=UpdateQueue(path/'state.sqlite')
            self.assertEqual(queue.append([update],path/'updates.jsonl'),0)
            queue.close()
            self.assertEqual(len((path/'updates.jsonl').read_text().splitlines()),1)
    def test_model_training_path_never_starts_update_receiver(self):
        import headline_model.main as application
        with tempfile.TemporaryDirectory() as temp:
            output=Path(temp)/'model.joblib'
            with patch('headline_model.main.load_env'),patch('headline_model.main.PositionRidge') as ridge,patch('headline_model.update_receiver.UpdateQueue') as queue,patch('headline_model.main.MetaClient') as muse,patch('sys.stdout',new=io.StringIO()):
                ridge.return_value.train.return_value={'trained':True}
                self.assertEqual(application.main(['train','--csv','reviewed.csv','--output',str(output)]),0)
                ridge.return_value.train.assert_called_once()
                queue.assert_not_called()
                muse.assert_not_called()


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.journal=Journal(Path(self.temp.name)/'j.sqlite')
        self.decision=HeadlineEngine(FixtureScorer(),self.journal,'.5',matcher=FixtureMatcher()).process(article(),portfolio(),NOW)[0]
        self.state=PositionState('kalshi','EXAMPLE','YES',Decimal(100),Decimal('.5'),NOW)
    def tearDown(self):
        self.journal.close()
        self.temp.cleanup()
    def test_paper_full_fill(self):
        broker=PaperBroker({'merger-yes':self.state})
        self.assertTrue(execute(self.decision,broker,'.45',NOW)['fully_filled'])
        self.assertEqual(broker.position('merger-yes').quantity,50)
    def test_partial_fill_not_complete_exit(self):
        broker=PaperBroker({'merger-yes':self.state},available_liquidity=Decimal(10))
        result=execute(self.decision,broker,'.45',NOW)
        self.assertFalse(result['fully_filled'])
        self.assertEqual(result['unfilled_quantity'],'40')
    def test_stale_wrong_side_and_changed_quantity_block(self):
        for state in [PositionState('kalshi','EXAMPLE','NO',Decimal(100),Decimal('.5'),NOW),PositionState('kalshi','EXAMPLE','YES',Decimal(10),Decimal('.5'),NOW),PositionState('kalshi','EXAMPLE','YES',Decimal(100),Decimal('.5'),NOW-timedelta(seconds=30))]:
            with self.assertRaises(ValueError):
                execute(self.decision,PaperBroker({'merger-yes':state}),'.45',NOW)
    def test_price_floor_blocks_sale(self):
        with self.assertRaises(ValueError):
            execute(self.decision,PaperBroker({'merger-yes':self.state}),'.9',NOW)


class ModelAndQueueTests(unittest.TestCase):
    def test_batch_prepare_preserves_human_label_without_training(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)
            p=position()
            row={'headline':article()['headline'],'published_at':NOW.isoformat(),'summary':'','position_id':p['id'],'market_question':p['question'],'position_thesis':p['thesis'],'outcome':p['outcome'],'resolution_rules':p['resolution_rules'],'deadline':p['deadline'],'sentiment':'-1'}
            pd.DataFrame([row]).to_csv(path/'raw.csv',index=False)
            client=SimpleNamespace(assess=lambda news,position:validate_assessment(evidence(),news,'fixture-model'))
            with patch('headline_model.main.MetaClient',return_value=client):
                result=prepare(SimpleNamespace(csv=path/'raw.csv',output=path/'review.csv'))
            self.assertFalse(result['trained'])
            frame=pd.read_csv(path/'review.csv')
            self.assertEqual(frame.sentiment.iloc[0],-1)
            self.assertEqual(frame.evidence_model.iloc[0],'fixture-model')
            self.assertFalse(list(path.glob('*.joblib')))
    def test_extraction_keeps_human_labels_blank_and_does_not_train(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)
            (path/'article.json').write_text(json.dumps(article()))
            (path/'portfolio.json').write_text(json.dumps(portfolio()))
            args=SimpleNamespace(article=path/'article.json',portfolio=path/'portfolio.json',output=path/'review.csv')
            with patch('headline_model.main.MetaClient',return_value=None),patch('headline_model.main.MuseMatcher',return_value=FixtureMatcher()):
                result=extract(args)
            self.assertFalse(result['trained'])
            frame=pd.read_csv(path/'review.csv',keep_default_na=False)
            self.assertEqual(frame.sentiment.iloc[0],'')
            self.assertEqual(len(frame.headline_id.iloc[0]),64)
            self.assertFalse(list(path.glob('*.joblib')))
    def test_extraction_and_preparation_preserve_the_same_evidence_schema(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)
            news=article(summary='Source excerpt, with a comma.')
            held=position(id='merger-no',outcome='NO',thesis='The Acme merger will not close.')
            (path/'article.json').write_text(json.dumps(news))
            (path/'portfolio.json').write_text(json.dumps(portfolio(positions=[held])))
            row={'headline':news['headline'],'published_at':news['published_at'],'summary':news['summary'],'position_id':held['id'],'market_question':held['question'],'position_thesis':held['thesis'],'outcome':held['outcome'],'resolution_rules':held['resolution_rules'],'deadline':held['deadline'],'sentiment':'1'}
            pd.DataFrame([row]).to_csv(path/'raw.csv',index=False)
            assessment=evidence(adverse_evidence=.02,favorable_evidence=.95)
            client=SimpleNamespace(assess=lambda supplied,p:validate_assessment(assessment,supplied,'fixture-model'))
            with patch('headline_model.main.MetaClient',return_value=client):
                extract(SimpleNamespace(article=path/'article.json',portfolio=path/'portfolio.json',output=path/'extracted.csv'))
                prepare(SimpleNamespace(csv=path/'raw.csv',output=path/'prepared.csv'))
            with (path/'extracted.csv').open(newline='') as source:
                reader=csv.DictReader(source)
                extraction_fields=reader.fieldnames
                extracted=list(reader)[0]
            with (path/'prepared.csv').open(newline='') as source:
                reader=csv.DictReader(source)
                self.assertEqual(reader.fieldnames,extraction_fields)
                prepared=list(reader)[0]
            self.assertEqual(extraction_fields,['headline_id','position_id','evidence_model','prompt_version',*FEATURES,'sentiment','related','confidence','headline','market_question','held_outcome','reason','evidence'])
            self.assertEqual(extracted.pop('sentiment'),'')
            self.assertEqual(prepared.pop('sentiment'),'1')
            self.assertEqual(extracted,prepared)
            self.assertEqual(prepared['held_outcome'],'NO')
            self.assertEqual(json.loads(prepared['evidence']),assessment['evidence'])
    def test_existing_extraction_output_does_not_make_model_calls(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)
            output=path/'review.csv'
            output.write_text('Existing reviewed data\n')
            args=SimpleNamespace(article=path/'unused.json',portfolio=path/'unused-portfolio.json',output=output)
            with patch('headline_model.main.MetaClient') as client,patch('headline_model.main.MuseMatcher') as matcher:
                with self.assertRaisesRegex(ValueError,'already exists'):
                    extract(args)
                client.assert_not_called()
                matcher.assert_not_called()
            self.assertEqual(output.read_text(),'Existing reviewed data\n')
    def test_real_ridge_on_temporary_math_fixture_only(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)
            rng=np.random.default_rng(42)
            rows=[]
            for i in range(180):
                label=[-1,0,1][i%3]
                row=dict(headline_id=f'math-fixture-{i}',position_id='fixture',evidence_model='fixture-model',prompt_version=PROMPT_VERSION,adverse_evidence=.9 if label==-1 else .05,favorable_evidence=.9 if label==1 else .05,uncertainty=.05,event_specificity=.9,time_alignment=.9,sentiment=label)
                for field in FEATURES:
                    row[field]=float(np.clip(row[field]+rng.normal(0,.01),0,1))
                rows.append(row)
            pd.DataFrame(rows).to_csv(path/'fixture.csv',index=False)
            report=PositionRidge().train(path/'fixture.csv',path/'fixture.joblib')
            self.assertGreater(report['holdout_macro_f1'],.9)
            model=PositionRidge(path/'fixture.joblib')
            self.assertLess(model.score(validate_assessment(evidence(),article(),'fixture-model')),-.5)
            with self.assertRaises(ValueError):
                model.score(validate_assessment(evidence(),article(),'different-model'))
    def test_empty_template_does_not_train(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)
            pd.DataFrame(columns=['headline_id','position_id','evidence_model','prompt_version',*FEATURES,'sentiment']).to_csv(path/'empty.csv',index=False)
            with self.assertRaises(ValueError):
                PositionRidge().train(path/'empty.csv',path/'never.joblib')
            self.assertFalse((path/'never.joblib').exists())
    def test_queue_partial_lines_and_restart_cursor(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)
            now=datetime.now(timezone.utc)
            news=article(published_at=now.isoformat())
            book=portfolio(as_of=now.isoformat(),positions=[position(deadline=(now+timedelta(days=1)).isoformat())])
            (path/'portfolio.json').write_text(json.dumps(book))
            (path/'headlines.jsonl').write_text(json.dumps(news))
            journal=Journal(path/'state.sqlite')
            engine=HeadlineEngine(FixtureScorer(),journal,'.5',matcher=FixtureMatcher())
            args=SimpleNamespace(headlines=path/'headlines.jsonl',portfolio=path/'portfolio.json')
            self.assertEqual(consume_queue(args,engine,journal),0)
            with (path/'headlines.jsonl').open('a') as out:
                out.write('\n')
            with patch('sys.stdout',new=io.StringIO()):
                self.assertEqual(consume_queue(args,engine,journal),1)
                self.assertEqual(consume_queue(args,engine,journal),0)
            journal.close()
            journal=Journal(path/'state.sqlite')
            self.assertEqual(consume_queue(args,engine,journal),0)
            journal.close()


if __name__=='__main__':
    unittest.main()
