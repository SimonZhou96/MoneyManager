"""Offline acceptance tests for the ETF research runner. No market API calls."""
import copy
import json
import math
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
from test_index_ema_backtest import backtest as bt, _session, _utc_text, _business_dates


def prepared_day(day=date(2025, 11, 3), overrides=None, base=102, half=False):
    rows = _session(day, first=(100, 100.1, 99.9, 100),
                    second=(100, 100.1, 99.9, 100), base=base, overrides=overrides)
    if half:
        rows = rows[:42]
    points = [dict(upper=101, lower=99, vwap=100, relative_volume=1) for r in rows]
    return {day: dict(rows=rows, points=points, atr=2.0, volatility=.01,
                     open=100.0, previous_close=100.0, atr_upper=101.0, atr_lower=99.0,
                     corporate_action={})}


def metadata(mu, k=1):
    return dict(predicted_return=mu, fee_multiplier=k, model_version='test-model',
                training_cutoff='2025-10-31T20:00:00+00:00')


class IntradayRulesTests(unittest.TestCase):
    def test_atr_breakout_next_open_and_close(self):
        day = date(2025, 11, 3)
        prep = prepared_day(overrides={2: (100, 102.1, 100, 102), 3: (102.2, 102.3, 102.1, 102.2)})
        result = bt.simulate_prepared('US.SPY', prep, 10000, bt.ATR_VARIANT)
        entry, exit = result['fills']
        self.assertEqual(entry['timestamp'], _utc_text(day, 9, 45))
        self.assertEqual(entry['signal_time'], _utc_text(day, 9, 45))
        self.assertEqual(entry['price'], 102.2)
        self.assertEqual(exit['timestamp'], _utc_text(day, 16, 0))
        self.assertEqual(exit['reason'], '收盘平仓')
        self.assertLessEqual(entry['quantity'] * entry['price'], 2 * (10000-entry['fee_usd']))

    def test_pullback_waits_for_next_checkpoint(self):
        prep = prepared_day(overrides={2:(100,102,100,102), 4:(103,103.1,102.9,103),
                                      5:(103,103,101.8,101.8), 6:(101.7,102,101.6,102)})
        plain = bt.simulate_prepared('US.SPY', prep, 10000, bt.ATR_VARIANT)
        pull = bt.simulate_prepared('US.SPY', prep, 10000, bt.PULLBACK_VARIANT)
        self.assertLess(plain['fills'][0]['timestamp'], pull['fills'][0]['timestamp'])
        self.assertEqual(pull['fills'][0]['timestamp'], _utc_text(date(2025,11,3),10,0))
        self.assertEqual(pull['fills'][0]['price'], 101.7)

    def test_pullback_expires_inside_band(self):
        prep = prepared_day(base=100, overrides={2:(100,102,100,102),4:(103,103,103,103),5:(103,103,100,100)})
        self.assertEqual(bt.simulate_prepared('US.SPY',prep,10000,bt.PULLBACK_VARIANT)['fills'], [])

    def test_stop_and_reverse_close_before_open_with_separate_fees(self):
        prep = prepared_day(base=98, overrides={2:(100,102,100,102),3:(102,102,102,102),5:(102,102,98,98),6:(97.5,98,97,98)})
        fills = bt.simulate_prepared('US.SPY',prep,10000,bt.ATR_VARIANT)['fills']
        self.assertEqual([f['action'] for f in fills], ['buy','sell','sell_short','buy_to_cover'])
        self.assertEqual(fills[1]['timestamp'],fills[2]['timestamp'])
        self.assertEqual(fills[1]['price'],97.5)
        self.assertTrue(all(f['fee_usd']>0 for f in fills))

    def test_half_day_is_calendar_driven_not_missing_afternoon(self):
        early = date(2025,11,28)
        prep = prepared_day(day=early, half=True)
        self.assertTrue(bt._is_complete_session(prep[early]['rows'],'US.SPY'))
        result=bt.simulate_prepared('US.SPY',prep,10000,bt.ATR_VARIANT)
        self.assertEqual(result['fills'][-1]['timestamp'],_utc_text(early,13,0))
        ordinary=prepared_day(half=True)
        self.assertFalse(bt._is_complete_session(next(iter(ordinary.values()))['rows'],'US.SPY'))

    def test_next_bar_gap_does_not_create_signal(self):
        prep=prepared_day(base=100)
        prep[date(2025,11,3)]['rows'][3]['open']=150
        self.assertEqual(bt.simulate_prepared('US.SPY',prep,10000,bt.ATR_VARIANT)['fills'],[])

    def test_ml_weak_opposite_holds_strong_opposite_reverses(self):
        day=date(2025,11,3);prep=prepared_day()
        preds={_utc_text(day,9,45):metadata(.01),_utc_text(day,10,0):metadata(-.000001),
               _utc_text(day,10,15):metadata(-.01)}
        result=bt.simulate_prepared('US.SPY',prep,10000,bt.TREE_VARIANT,predictions=preds)
        self.assertEqual([f['action'] for f in result['fills']],['buy','sell','sell_short','buy_to_cover'])
        self.assertEqual(result['fills'][1]['timestamp'],_utc_text(day,10,15))
        self.assertEqual(result['summary']['average_r'],None)
        self.assertEqual(result['summary']['open_positions'],0)
        self.assertAlmostEqual(result['summary']['net_profit_usd'],
                               result['summary']['gross_profit_usd']-result['summary']['total_fees_usd'],places=1)
        self.assertEqual(result['fills'][0]['model_version'],'test-model')
        self.assertGreater(result['fills'][2]['fee_threshold_usd'],result['fills'][0]['fee_threshold_usd'])

    def test_no_qualified_validation_means_no_trading(self):
        day=date(2025,11,3);prep=prepared_day()
        preds={_utc_text(day,9,45):metadata(1,None)}
        result=bt.simulate_prepared('US.SPY',prep,10000,bt.MLP_VARIANT,predictions=preds)
        self.assertFalse(result['fills'])
        self.assertEqual(result['summary']['final_equity_usd'],10000)

    def test_minimum_fees_prevent_tiny_edge(self):
        day=date(2025,11,3);prep=prepared_day()
        result=bt.simulate_prepared('US.SPY',prep,10000,bt.TREE_VARIANT,
            predictions={_utc_text(day,9,45):metadata(.000001)})
        self.assertFalse(result['fills'])
        self.assertGreater(bt._fee_usd('US.SPY','buy',1,1000),1.99)

    def test_bankruptcy_prevents_new_entries(self):
        prep=prepared_day(base=10,overrides={2:(100,102,100,102),3:(102,102,102,102),5:(10,10,10,10),6:(10,10,10,10)})
        day=date(2025,11,3);prep[day]['volatility']=.001
        preds={_utc_text(day,9,45):metadata(.5),_utc_text(day,10,15):metadata(.5)}
        result=bt.simulate_prepared('US.SPY',prep,10000,bt.TREE_VARIANT,predictions=preds)
        self.assertEqual(len(result['fills']),2)
        self.assertLessEqual(result['summary']['final_equity_usd'],0)
        self.assertIn('净值耗尽',result['fills'][-1]['reason'])


class DataAndFeatureTests(unittest.TestCase):
    def test_actions_use_base_ert_and_reject_unsupported_actions(self):
        row=dict(ex_div_date='2025-11-20',forward_adj_factorA=.5,forward_adj_factorB=0,
                 per_cash_div=float('nan'),split_base=1,split_ert=2,split_ratio=.5)
        a=bt._normalise_actions(pd.DataFrame([row]))[date(2025,11,20)]
        self.assertEqual(a['share_factor'],2)
        self.assertEqual(a['cash_dividend'],0)
        row['allotment_ratio']=.1
        with self.assertRaisesRegex(RuntimeError,'公司行动'):
            bt._normalise_actions(pd.DataFrame([row]))

    def test_split_adjusts_prior_atr_not_raw_fills_and_not_past_outputs(self):
        days=_business_dates(date(2025,9,2),17)
        bars=[]
        for day in days:
            bars+=_session(day,first=(100,102,98,100),second=(100,102,98,100),base=100)
        original=bt.prepare_symbol(bars,'US.TQQQ')
        before=copy.deepcopy(original[days[-2]])
        for row in bars[-78:]:
            for key in ('open','high','low','close'):row[key]/=2
            row['volume']*=2
        bars[-78]['corporate_action']=dict(price_a=.5,price_b=0,share_factor=2,cash_dividend=0)
        after=bt.prepare_symbol(bars,'US.TQQQ')
        self.assertEqual(before,after[days[-2]])
        self.assertEqual(after[days[-1]]['previous_close'],50)
        self.assertAlmostEqual(after[days[-1]]['atr'],original[days[-1]]['atr']/2)
        self.assertEqual(after[days[-1]]['points'][5]['relative_volume'],1)

    def test_hold_receives_split_and_dividend_without_reinvesting(self):
        d1,d2=date(2025,11,19),date(2025,11,20)
        prep=prepared_day(d1,base=100)
        second=prepared_day(d2,base=50)[d2]
        second['open']=50
        second['corporate_action']=dict(share_factor=2,cash_dividend=1,price_a=.5,price_b=0)
        prep[d2]=second
        result=bt._buy_and_hold_prepared('US.TQQQ',prep,10000,d1,'full')
        # 99 shares purchased, 198 shares after the split; $99 cash entitlement.
        entry_fee=bt._fee_usd('US.TQQQ','buy',99,100)
        exit_fee=bt._fee_usd('US.TQQQ','sell',198,50)
        self.assertAlmostEqual(result['summary']['final_equity_usd'],round(10000+99-entry_fee-exit_fee,2))
        self.assertEqual(result['summary']['cash_dividends_usd'],99)

    def test_15_minute_features_and_labels_use_correct_boundaries(self):
        day=date(2025,11,3);all_prepared={s:prepared_day() for s in bt.SYMBOLS}
        samples=bt.build_samples(all_prepared)['US.SPY']
        first=samples[0]
        self.assertEqual(first['signal_time'],_utc_text(day,9,45))
        self.assertEqual(first['label_end'],_utc_text(day,10,0))
        self.assertTrue(math.isnan(first['features'][2]))
        self.assertTrue(math.isnan(first['features'][3]))
        self.assertEqual(samples[-1]['label_end'],_utc_text(day,16,0))
        self.assertEqual(len(first['features']),len(bt.FEATURE_NAMES))
        changed=copy.deepcopy(all_prepared)
        changed['US.SPY'][day]['rows'][3]['open']=300
        changed['US.SPY'][day]['rows'][6]['open']=50
        new=bt.build_samples(changed)['US.SPY'][0]
        np.testing.assert_equal(first['features'],new['features'])
        self.assertNotEqual(first['label'],new['label'])
        self.assertAlmostEqual(new['label'],50/300-1)

    def test_half_day_labels_end_at_1300(self):
        day=date(2025,11,28)
        samples=bt.build_samples({s:prepared_day(day,half=True) for s in bt.SYMBOLS})['US.SPY']
        self.assertEqual(len(samples),13)
        self.assertEqual(samples[-1]['label_end'],_utc_text(day,13,0))

    def test_missing_cross_market_bar_fails(self):
        with self.assertRaisesRegex(RuntimeError,'同步行情'):
            bt.build_samples({'US.QQQ':prepared_day()})


class TrainingTests(unittest.TestCase):
    def test_network_matrix_is_three_widths_by_three_seeds(self):
        self.assertEqual(len(bt.NETWORK_CONFIGS),9)
        self.assertEqual(len(bt.ALL_VARIANTS),16)
        for size,layers in bt.NETWORK_ARCHITECTURES.items():
            for seed in (42,43,44):
                variant=f'ml_mlp_{size}_seed{seed}'
                model=bt.make_model(variant)
                estimator=model.regressor.steps[-1][1]
                self.assertEqual(estimator.hidden_layer_sizes,layers)
                self.assertFalse(estimator.early_stopping)
                self.assertEqual(bt.NETWORK_CONFIGS[variant]['seed'],seed)

    def test_checkpoint_training_and_refit_use_identical_optimizer_history(self):
        rows=[dict(features=[i/100,math.sin(i)],label=.001*math.sin(i)) for i in range(300)]
        from threadpoolctl import threadpool_limits
        with threadpool_limits(limits=1):
            checkpoints=list((epoch,bt._predict(model,rows[:10])) for epoch,model in
                             bt._neural_checkpoints('ml_mlp_small_seed42',rows,.1,(2,4)))
            refit=bt._fit_model('ml_mlp_small_seed42',rows,alpha=.1,epochs=4)
            np.testing.assert_allclose(checkpoints[-1][1],bt._predict(refit,rows[:10]),rtol=0,atol=0)
            other=bt._fit_model('ml_mlp_small_seed43',rows,alpha=.1,epochs=4)
            self.assertFalse(np.allclose(bt._predict(refit,rows[:10]),bt._predict(other,rows[:10])))

    def test_network_selection_only_trains_on_training_rows(self):
        from types import SimpleNamespace
        train=[dict(features=[1],label=.01)]
        validation=[dict(features=[999],label=-999)]
        seen=[]
        model=SimpleNamespace(regressor_=SimpleNamespace(steps=[('mlp',SimpleNamespace(
            coefs_=[np.zeros((2,2))],intercepts_=[np.zeros(2)]))]))
        def checkpoints(variant,rows,alpha,epochs):
            self.assertIs(rows,train);seen.append(alpha)
            for epoch in epochs:yield epoch,model
        def choices(*args):
            self.assertIs(args[3],validation)
            return 2,[dict(k=2,trades=25,net_profit_usd=10,sharpe=1,turnover=10)]
        with patch.object(bt,'_neural_checkpoints',side_effect=checkpoints),patch.object(bt,'_predict',return_value=[.01]),patch.object(bt,'choose_fee_multiplier',side_effect=choices):
            selected,candidates,count=bt.select_network_parameters('US.SPY',{},'ml_mlp_large_seed42',
                train,validation,date(2025,1,1),date(2025,7,1))
        self.assertEqual(seen,[.01,.1])
        self.assertEqual(len(candidates),6)
        self.assertEqual(count,6)
        self.assertEqual(selected['epochs'],50)
        self.assertEqual(selected['alpha'],.1)

    def test_network_validation_rejects_unprofitable_candidates(self):
        from types import SimpleNamespace
        model=SimpleNamespace(regressor_=SimpleNamespace(steps=[('mlp',SimpleNamespace(coefs_=[],intercepts_=[]))]))
        with patch.object(bt,'_neural_checkpoints',side_effect=lambda *args:iter([(50,model)])),patch.object(bt,'_predict',return_value=[]),patch.object(bt,'choose_fee_multiplier',return_value=(None,[dict(k=1,trades=100,net_profit_usd=-1,sharpe=1,turnover=1)])):
            chosen,candidates,_=bt.select_network_parameters('US.SPY',{},'ml_mlp_large_seed42',[],[],date(2025,1,1),date(2025,7,1))
        self.assertIsNone(chosen)
        self.assertEqual(len(candidates),2)

    def test_real_models_fit_deterministically_and_handle_missing_features(self):
        rows=[dict(features=[math.nan if i%3==0 else i/100, math.sin(i)],label=.001*math.sin(i)) for i in range(300)]
        from threadpoolctl import threadpool_limits
        with threadpool_limits(limits=1):
            for variant in bt.ML_VARIANTS:
                kwargs=dict(epochs=4) if variant in bt.NETWORK_CONFIGS else {}
                a=bt._fit_model(variant,rows,**kwargs);b=bt._fit_model(variant,rows,**kwargs)
                np.testing.assert_allclose(bt._predict(a,rows[:10]),bt._predict(b,rows[:10]))
                self.assertTrue(np.isfinite(bt._predict(a,rows)).all())
                if variant in bt.NETWORK_CONFIGS:
                    self.assertEqual(len(a.regressor_.steps[-1][1].loss_curve_),4)
                else:
                    self.assertEqual(a.regressor_.steps[-1][1].max_iter,200)

    def test_validation_requires_positive_profit_and_twenty_trades(self):
        results=[dict(summary=dict(net_profit_usd=5,closed_trades=19,sharpe_ratio=10),fills=[]),
                 dict(summary=dict(net_profit_usd=-1,closed_trades=21,sharpe_ratio=11),fills=[]),
                 dict(summary=dict(net_profit_usd=1,closed_trades=20,sharpe_ratio=.5),fills=[])]
        with patch.object(bt,'simulate_prepared',side_effect=results):
            k,candidates=bt.choose_fee_multiplier('US.SPY',{},bt.TREE_VARIANT,[],[],date(2025,1,1),date(2025,7,1))
        self.assertEqual(k,3)
        with patch.object(bt,'simulate_prepared',return_value=results[0]):
            self.assertIsNone(bt.choose_fee_multiplier('US.SPY',{},bt.TREE_VARIANT,[],[],date(2025,1,1),date(2025,7,1))[0])

    def test_validation_tie_chooses_lower_turnover(self):
        results=[dict(summary=dict(net_profit_usd=1,closed_trades=20,sharpe_ratio=1),
                      fills=[dict(price=100,quantity=q)]) for q in (30,10,20)]
        with patch.object(bt,'simulate_prepared',side_effect=results):
            self.assertEqual(bt.choose_fee_multiplier('US.SPY',{},bt.TREE_VARIANT,[],[],date(2025,1,1),date(2025,7,1))[0],2)

    def test_rolling_fit_cutoffs_and_future_labels_do_not_change_old_predictions(self):
        days=_business_dates(date(2025,1,2),110)
        samples={'US.SPY':[dict(date=d,signal_time=_utc_text(d,9,45),label_end=_utc_text(d,10,0),
                               features=[1],label=i*.00001) for i,d in enumerate(days)]}
        fits=[]
        def fit(variant,rows,**kwargs):
            fits.append([r['date'] for r in rows]);return sum(r['label'] for r in rows)/len(rows)
        def predict(model,rows):return [model]*len(rows)
        with patch.multiple(bt,TRAIN_MONTHS=1,VALIDATION_MONTHS=1),patch.object(bt,'_fit_model',side_effect=fit),patch.object(bt,'_predict',side_effect=predict),patch.object(bt,'choose_fee_multiplier',return_value=(1,[])),patch.object(bt,'select_network_parameters',return_value=(dict(k=1,alpha=.01,epochs=50),[],100)):
            preds,folds=bt.train_walk_forward({'US.SPY':{}},samples,progress=False,workers=1)
            changed=copy.deepcopy(samples)
            for r in changed['US.SPY']:
                if r['date']>=date(2025,5,1):r['label']=999
            newer,_=bt.train_walk_forward({'US.SPY':{}},changed,progress=False,workers=1)
        for variant in bt.ML_VARIANTS:
            for stamp,value in preds['US.SPY'][variant].items():
                if stamp<'2025-05-01':self.assertEqual(value,newer['US.SPY'][variant][stamp])
        for fold in folds:
            self.assertLess(fold['training_cutoff'][:10],fold['test_start'])
            self.assertGreater(fold['train_samples'],0)
            if fold['strategy_variant'] in bt.NETWORK_CONFIGS:
                self.assertIn(fold['model_random_seed'],bt.NETWORK_SEEDS)
                self.assertEqual(fold['model_epochs'],50)
                self.assertLess(fold['selection_cutoff'][:10],fold['test_start'])
        self.assertEqual(folds[0]['train_start'],'2025-02-01')
        self.assertEqual(folds[0]['validation_start'],'2025-03-01')
        self.assertEqual(folds[0]['test_start'],'2025-04-01')

    def test_short_history_does_not_silently_train_in_sample(self):
        samples={'US.SPY':[dict(date=date(2025,1,2)),dict(date=date(2025,12,31))]}
        with self.assertRaisesRegex(RuntimeError,'历史长度不足'):
            bt.walk_forward_months(samples)


class OutputAndLedgerTests(unittest.TestCase):
    def test_two_periods_reset_capital_and_keep_disjoint_ledger_ids(self):
        days=_business_dates(date(2025,6,2),20)
        prep={symbol:{} for symbol in bt.SYMBOLS}
        data={symbol:[] for symbol in bt.SYMBOLS}
        preds={symbol:{variant:{} for variant in bt.ML_VARIANTS} for symbol in bt.SYMBOLS}
        for symbol in bt.SYMBOLS:
            for day in days:
                info=prepared_day(day,base=102)[day]
                info['rows'][3]['open']=101
                prep[symbol][day]=info
                data[symbol]+=info['rows']
                for variant in bt.ML_VARIANTS:
                    preds[symbol][variant][_utc_text(day,9,45)]=metadata(.1)
        with patch.object(bt,'RECENT_PERIOD_TRADING_DAYS',5):
            result=bt.run_scenarios(data,prepared_by_symbol=prep,predictions=preds,folds=[],evaluation_start=days[0])
        self.assertEqual(len(result['scenarios']),96)
        self.assertEqual(len(result['benchmarks']),6)
        self.assertEqual(result['recent_year_start'],days[-5].isoformat())
        full=next(s for s in result['scenarios'] if s['symbol']=='US.SPY' and s['strategy_variant']==bt.TREE_VARIANT and s['evaluation_period']=='full')
        recent=next(s for s in result['scenarios'] if s['symbol']=='US.SPY' and s['strategy_variant']==bt.TREE_VARIANT and s['evaluation_period']=='recent_year')
        self.assertEqual(full['initial_cash_usd'],recent['initial_cash_usd'])
        full_entry=next(f for f in full['fills'] if f['session_date']==days[-5].isoformat() and f['role']=='entry')
        self.assertNotEqual(full_entry['quantity'],recent['fills'][0]['quantity'])
        self.assertTrue(set(f['trade_id'] for f in full['fills']).isdisjoint(f['trade_id'] for f in recent['fills']))
        for scenario in result['scenarios']:
            summary=scenario['summary']
            total=sum(f.get('net_pnl_usd',0) for f in scenario['fills'])
            self.assertAlmostEqual(summary['net_profit_usd'],round(total,2),places=2)
            self.assertEqual(summary['open_positions'],0)
        with tempfile.TemporaryDirectory() as tmp:
            html_path,json_path=bt.write_outputs(data,result,Path(tmp),prepared_by_symbol=prep)
            self.assertEqual(set(p.name for p in Path(tmp).iterdir()),{'index_ema_backtest.html','trades.json'})
            ledger=json.loads(json_path.read_text())
            payload=bt._chart_payload(data,result,prep)
            self.assertEqual(payload['fills'],ledger)
            self.assertEqual(len(payload['panels']),96)
            self.assertTrue(all(f['bar_timestamp'] and f['signal_time'] for f in ledger))
            self.assertIn('prediction_signal_time',next(f for f in ledger if f['model_version']))
            self.assertEqual(len(payload['network_comparison']),18)
            group=payload['network_comparison'][0]
            self.assertEqual(group['seeds'],[42,43,44])
            self.assertEqual(len(group['members']),3)
            self.assertEqual(group['min_return_pct'],group['max_return_pct'])
            self.assertEqual(group['beats_small_seeds'],0)
            self.assertIn('network-comparison',html_path.read_text())
            for scenario in result['scenarios']:
                self.assertAlmostEqual(sum(m['net_profit_usd'] for m in scenario['monthly_performance']),scenario['summary']['net_profit_usd'],places=2)
                self.assertAlmostEqual(sum(m['total_fees_usd'] for m in scenario['monthly_performance']),scenario['summary']['total_fees_usd'],places=2)

    def test_chart_marker_bar_is_execution_bar_and_eod_last_bar(self):
        day=date(2025,11,3)
        result=bt.simulate_prepared('US.SPY',prepared_day(),10000,bt.ATR_VARIANT)
        self.assertEqual(result['fills'][0]['bar_timestamp'],_utc_text(day,9,45))
        self.assertEqual(result['fills'][-1]['bar_timestamp'],_utc_text(day,15,55))

    def test_api_limiter_counts_retries_and_stops_after_three_attempts(self):
        limiter=bt.QuoteLimiter()
        with patch.object(bt.clock,'sleep'):
            with self.assertRaisesRegex(RuntimeError,'3 次失败'):
                limiter.call(lambda:(-1,'quota'))
        self.assertEqual(limiter.count,3)

    def test_fill_preserves_full_execution_precision(self):
        prep=prepared_day(overrides={3:(102.123456,102.2,102.1,102.2)})
        result=bt.simulate_prepared('US.SPY',prep,10000,bt.ATR_VARIANT)
        self.assertEqual(result['fills'][0]['price'],102.123456)


if __name__=='__main__':unittest.main()
