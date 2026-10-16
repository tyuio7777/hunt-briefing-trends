import copy
import importlib.util
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('collector', Path(__file__).parents[1] / 'tools/collect.py')
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)
NOW = datetime(2026, 10, 16, 6, 30, tzinfo=timezone.utc)
RULES = c.read(Path(__file__).parents[1] / 'config/topics.json')
OVERRIDES = {'articles': {}, 'url_aliases': {}}
def issue(day):
    return {'id': day, 'title': '브리핑', 'published_at': day+'T09:00:00+09:00',
        'json_url': c.FEED+'feed/'+day+'.json', 'html_url': c.FEED+'feed/'+day+'.html'}
def snapshot(day, sections=None, events=None):
    return {'issue': issue(day), 'document': {'sections': sections or [], 'calendar_events': events or []}}
def event(**kw):
    d = {'event_id': 'event-one', 'title':'이벤트', 'summary':'공식 일정', 'start_date':'2026-10-16',
         'end_date':'2026-10-20', 'status':'confirmed', 'source_url':'https://example.org/news/event'}
    d.update(kw)
    return d
def supplements(events=None):
    return {'calendar_events': events or [], 'event_aliases': {}, 'event_details': {}}

class SourceTests(unittest.TestCase):
    def test_tracking_and_fragment(self):
        self.assertEqual(c.url_key('http://www.example.org/news/a/?utm_source=x#top'), 'https://example.org/news/a')
    def test_reddit_identity(self):
        self.assertEqual(c.url_key('https://old.reddit.com/r/hunt/comments/abc/title/'),c.url_key('https://www.reddit.com/comments/abc/other'))
    def test_youtube_identity(self):
        self.assertEqual(c.url_key('https://youtu.be/Abc_1234567?t=5'),c.url_key('https://www.youtube.com/watch?v=Abc_1234567&list=x'))
    def test_listing_excluded(self):
        for url in ['https://steamcommunity.com/app/594650/announcements/', 'https://www.youtube.com/@abc', 'https://www.reddit.com/r/HuntShowdown/', 'https://example.com/news', 'https://store.steampowered.com/app/594650/']:
            self.assertIsNone(c.url_key(url))
    def test_no_script_or_credentials(self):
        for url in ['javascript:alert(1)', 'https://user:password@example.com/news/a']:
            self.assertIsNone(c.url_key(url))
    def test_feed_reference_boundary(self):
        self.assertTrue(c.safe_feed_url(c.FEED+'feed/2026/10/x.json','json'))
        self.assertFalse(c.safe_feed_url(c.FEED+'feed/../secret.json','json'))
        self.assertFalse(c.safe_feed_url('https://evil.example/feed/x.json','json'))
    def test_rich_and_flat_dedup(self):
        rich = {'label':'OFFICIAL', 'items':[{'title':'무기 변경', 'sources':[{'url':'https://example.com/news/patch'}]}]}
        flat = {'category':'official','title':'이벤트 변경','source_urls':['https://example.com/news/patch?utm_source=x']}
        registry, _ = c.topic_registry([snapshot('2026-10-12',[flat]),snapshot('2026-10-03',[rich])],RULES,OVERRIDES)
        self.assertEqual(len(registry),1)
        self.assertEqual(registry[0]['first_included_at'],issue('2026-10-03')['published_at'])
        self.assertEqual(set(registry[0]['topics']),{'balance','events'})
        self.assertEqual(len(registry[0]['briefings']),2)
    def test_independent_opinions_not_merged(self):
        sections=[{'title':'무기 평가','source_urls':['https://reddit.com/comments/'+x]} for x in ('aaa','bbb')]
        self.assertEqual(len(c.topic_registry([snapshot('2026-10-12',sections)],RULES,OVERRIDES)[0]),2)
    def test_manual_translation_group(self):
        sections=[{'title':'이벤트','source_urls':['https://example.com/news/a','https://example.jp/news/a']}]
        override={'url_aliases':{'https://example.jp/news/a':'https://example.com/news/a'}}
        self.assertEqual(len(c.topic_registry([snapshot('2026-10-12',sections)],RULES,override)[0]),1)
    def test_zero_denominator(self):
        d=c.topics_feed([],[],RULES,NOW)
        self.assertTrue(all(t['share'] is None for t in d['topics']))
        c.validate_feed('topics',d)
    def test_period_kst_boundary(self):
        a={'first_included_at':'2026-10-09T15:00:00Z','topics':['events'],'briefings':[]}
        d=c.topics_feed([a],[],RULES,NOW)
        self.assertEqual(d['total'],1)
        a['first_included_at']='2026-10-09T14:59:59Z'
        self.assertEqual(c.topics_feed([a],[],RULES,NOW)['previous_total'],1)
    def test_unclassified_preserved(self):
        sec={'title':'최신 영상','category':'rumor','source_urls':['https://example.com/news/x']}
        r,_=c.topic_registry([snapshot('2026-10-12',[sec])],RULES,OVERRIDES)
        self.assertEqual(r[0]['origins'],['unconfirmed'])
        self.assertEqual(c.topics_feed(r,[],RULES,NOW)['topics'][-1]['count'],1)
    def test_unknown_schema_rejected(self):
        with self.assertRaises(ValueError): list(c.items({'new_shape':[]}))

class EventTests(unittest.TestCase):
    def test_date_only_no_fake_time(self):
        e=c.normalize_event(event())
        self.assertIsNone(e['start_at'])
        self.assertEqual(e['start_date'],'2026-10-16')
    def test_utc_rollover(self):
        e=c.normalize_event(event(start_at='2026-10-16T20:00:00'))
        self.assertEqual((e['start_date'],e['end_date']),('2026-10-17','2026-10-21'))
    def test_reverse_time_rejected(self):
        with self.assertRaises(ValueError): c.normalize_event(event(end_date='2026-10-15'))
    def test_unrelated_timestamp_rejected(self):
        with self.assertRaises(ValueError): c.normalize_event(event(start_at='2026-10-25T00:00:00Z'))
    def test_latest_cancelled_wins(self):
        d=c.events_feed([snapshot('2026-10-10',events=[event()]),snapshot('2026-10-12',events=[event(status='cancelled')])],supplements(),NOW)
        self.assertEqual(d['events'][0]['status'],'cancelled')
        self.assertEqual(len(d['events'][0]['briefings']),2)
    def test_long_event_not_limited_to_25(self):
        snapshots=[snapshot('2026-09-01',events=[event(start_date='2026-09-01',end_date='2026-12-01')])]
        snapshots += [snapshot('2026-09-'+str(i).zfill(2)) for i in range(2,30)]
        self.assertEqual(len(c.events_feed(snapshots,supplements(),NOW)['events']),1)
    def test_old_supplement_does_not_undo_new_cancel(self):
        sup=event(verified_at='2026-10-10T00:00:00Z',evidence='source')
        d=c.events_feed([snapshot('2026-10-12',events=[event(status='cancelled')])],supplements([sup]),NOW)
        self.assertEqual(d['events'][0]['status'],'cancelled')
    def test_supplement_requires_evidence(self):
        with self.assertRaises(ValueError): c.events_feed([],supplements([event()]),NOW)
    def test_milestone_not_expanded(self):
        e=c.normalize_event(event(start_date='2026-10-16',end_date='2026-10-16'))
        self.assertEqual(e['start_date'],e['end_date'])
    def test_server_notice_requires_evidence_and_expiry(self):
        s=supplements(); s['server_notices']=[{'region':'asia','status':'outage'}]
        with self.assertRaises(ValueError): c.events_feed([],s,NOW)
    def test_verified_server_notice(self):
        s=supplements(); s['server_notices']=[{'id':'test-only','title':'TEST','summary':'TEST',
            'region':'us-west','status':'degraded','source_url':'https://www.huntshowdown.com/news/test',
            'verified_at':c.iso(NOW),'expires_at':c.iso(NOW+timedelta(hours=1)),'evidence':'test fixture only'}]
        self.assertEqual(len(c.events_feed([],s,NOW)['server_notices']),1)
    def test_notice_cannot_be_indefinite(self):
        s=supplements(); s['server_notices']=[{'id':'test-only','title':'TEST','region':'global','status':'outage',
            'source_url':'https://www.huntshowdown.com/news/test', 'verified_at':c.iso(NOW),
            'expires_at':c.iso(NOW+timedelta(days=8)),'evidence':'test fixture only'}]
        with self.assertRaises(ValueError): c.events_feed([],s,NOW)

class ObservationTests(unittest.TestCase):
    def test_true_zero_allowed(self):
        self.assertEqual(c.player_record({'response':{'result':1,'player_count':0}},NOW)['count'],0)
    def test_bad_response_not_zero(self):
        for payload in [{},{'response':{'result':2,'player_count':0}},{'response':{'result':1,'player_count':-1}},{'response':{'result':1,'player_count':True}}]:
            with self.assertRaises(ValueError): c.player_record(payload,NOW)
    def test_slot_dedup(self):
        r=c.player_record({'response':{'result':1,'player_count':100}},NOW)
        d=c.player_feed([r,r],NOW)
        self.assertEqual(len(d['points']),1)
    def test_actual_time_preserved(self):
        d=NOW.replace(minute=41)
        r=c.player_record({'response':{'result':1,'player_count':123}},d)
        self.assertEqual(r['observed_at'],c.iso(d))
        self.assertNotEqual(r['observed_at'],r['slot'])
    def test_initial_empty_is_not_zero(self):
        d=c.player_feed([],NOW,False)
        self.assertIsNone(d['last_observed_at'])
        self.assertEqual(d['points'],[])
        self.assertEqual(d['status'],'error')
    def test_failure_preserves_valid_point(self):
        r=c.player_record({'response':{'result':1,'player_count':55}},NOW-timedelta(hours=1))
        d=c.player_feed([r],NOW,False)
        self.assertEqual(d['points'][0]['count'],55)
    def test_gap_not_filled(self):
        records=[c.player_record({'response':{'result':1,'player_count':20}},NOW-timedelta(hours=h)) for h in (0,5)]
        self.assertEqual(len(c.player_feed(records,NOW)['points']),2)
    def test_heatmap_requires_four_weeks(self):
        records=[c.player_record({'response':{'result':1,'player_count':20}},NOW-timedelta(hours=h)) for h in range(20)]
        self.assertTrue(all(x['median'] is None for x in c.player_feed(records,NOW)['heatmap']))
    def test_heatmap_sparse_cells_are_null(self):
        records=[c.player_record({'response':{'result':1,'player_count':20}},NOW-timedelta(days=d)) for d in (0,7,14,21,29)]
        d=c.player_feed(records,NOW)
        self.assertTrue(d['heatmap_ready'])
        self.assertTrue(all(x['median'] is None for x in d['heatmap']))
    def test_known_median_after_coverage(self):
        records=[c.player_record({'response':{'result':1,'player_count':v}},NOW-timedelta(days=d,minutes=m)) for d in (0,7,14,21) for m,v in ((0,100),(30,200))]
        records.append(c.player_record({'response':{'result':1,'player_count':50}},NOW-timedelta(days=29)))
        values=[x['median'] for x in c.player_feed(records,NOW)['heatmap'] if x['median'] is not None]
        self.assertEqual(values,[150])

if __name__=='__main__': unittest.main()
