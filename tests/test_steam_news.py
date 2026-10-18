import copy
import tempfile
import unittest
from pathlib import Path
from datetime import timedelta
from unittest.mock import patch
from test_collect import c, NOW

def item(gid='123', age=1, **changes):
    value = {'gid': gid, 'title': 'Official &amp; News', 'date': int((NOW-timedelta(days=age)).timestamp()),
        'appid': 594650, 'feedname': c.NEWS_FEED,
        'url': 'https://steamstore-a.akamaihd.net/news/externalpost/steam_community_announcements/'+gid,
        'is_external_url': True}
    value.update(changes)
    return value

def payload(*items):
    return {'appnews': {'appid': 594650, 'newsitems': list(items)}}

class SteamNewsTests(unittest.TestCase):
    def test_official_only_and_no_external_flag_misclassification(self):
        news = c.steam_news_feed(payload(item(), item('124', feedname='pcgamer')), NOW)
        self.assertEqual(len(news['items']), 1)
        self.assertEqual(news['items'][0]['title'], 'Official & News')
        self.assertEqual(news['source_checked_at'], c.iso(NOW))
        self.assertNotIn('contents', news['items'][0])
        c.validate_steam_news(news)

    def test_retention_dedup_sort_and_limit(self):
        rows = [item(str(i+1), age=i) for i in range(25)] + [item('1'), item('98',age=91), item('99',age=-1)]
        news = c.steam_news_feed(payload(*reversed(rows)), NOW)
        self.assertEqual(len(news['items']),20)
        self.assertEqual(len({i['id'] for i in news['items']}),20)
        self.assertEqual(news['items'][-1]['id'],'20')
        self.assertFalse({'98','99'} & {i['id'] for i in news['items']})

    def test_empty_is_only_a_valid_successful_response(self):
        news = c.steam_news_feed(payload(), NOW)
        self.assertEqual((news['status'],news['items']),('ok',[]))
        for value in ({}, {'appnews': {'appid': 1, 'newsitems': []}}, {'appnews': {'appid':594650}}, payload(item(feedname='pcgamer'))):
            with self.subTest(value=value), self.assertRaises(ValueError): c.steam_news_feed(value,NOW)

    def test_bad_official_item_cannot_erase_cached_news(self):
        for changes in ({'appid':1},{'gid':'x'},{'date':True},{'date':-1},{'title':''},{'title':'x'*501},
                        {'url':'https://steamcommunity.com.evil.example/news/1'},
                        {'url':'https://steamcommunity.com/app/1/announcements/detail/123'},
                        {'url':'https://user:pass@steamcommunity.com/app/594650/announcements/detail/123'},
                        {'url':'javascript:alert(1)'},{'url':'https://store.steampowered.com/news/app/594650/view/123?next=evil'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                c.steam_news_feed(payload(item(**changes)),NOW)

    def test_official_source_urls(self):
        for url in ('https://store.steampowered.com/news/app/594650/view/123',
                    'https://steamcommunity.com/games/594650/announcements/detail/123',
                    'https://steamcommunity.com/app/594650/announcements/detail/123',
                    item()['url']):
            self.assertTrue(c.safe_news_url(url))

    def test_cached_document_validation(self):
        good=c.steam_news_feed(payload(item()),NOW)
        for mutation in (lambda d:d.update(appid=1),lambda d:d.update(source_checked_at=None),
                         lambda d:d['items'].append(copy.deepcopy(d['items'][0])),
                         lambda d:d['items'][0].update(published_at=c.iso(NOW+timedelta(days=1)))):
            value=copy.deepcopy(good);mutation(value)
            with self.assertRaises(ValueError):c.validate_steam_news(value)

    def test_failure_preserves_last_success_and_recovers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); path=root/'data/steam-news.json'
            untouched=root/'data/players.json'; c.write(untouched,{'sentinel':1})
            with patch.object(c,'get_json',return_value=payload(item())) as get:
                self.assertIsNone(c.collect_steam_news(root,NOW))
                get.assert_called_once_with(c.STEAM_NEWS)
            good=c.read(path)
            with patch.object(c,'get_json',side_effect=OSError('offline')):
                self.assertEqual(c.collect_steam_news(root,NOW+timedelta(hours=1)),'steam_news: OSError')
            failed=c.read(path)
            self.assertEqual(failed['items'],good['items'])
            self.assertEqual(failed['generated_at'],good['generated_at'])
            self.assertEqual(failed['source_checked_at'],good['source_checked_at'])
            self.assertEqual(failed['status'],'error')
            c.validate_steam_news(failed)
            with patch.object(c,'get_json',return_value=payload(item('125'))):
                self.assertIsNone(c.collect_steam_news(root,NOW+timedelta(hours=2)))
            self.assertEqual(c.read(path)['items'][0]['id'],'125')
            self.assertEqual(c.read(untouched),{'sentinel':1})

    def test_first_failure_is_not_successful_empty(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(c,'get_json',side_effect=OSError('offline')):
            root=Path(tmp);self.assertIsNotNone(c.collect_steam_news(root,NOW))
            news=c.read(root/'data/steam-news.json')
            self.assertEqual(news['status'],'error');self.assertIsNone(news['source_checked_at'])
            c.validate_steam_news(news)

    def test_expired_items_are_pruned_even_when_offline(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); path=root/'data/steam-news.json'
            c.write(path,c.steam_news_feed(payload(item(age=89)),NOW))
            with patch.object(c,'get_json',side_effect=OSError('offline')):
                c.collect_steam_news(root,NOW+timedelta(days=2))
            news=c.read(path)
            self.assertEqual(news['items'],[]);self.assertEqual(news['status'],'error')

    def test_run_news_failure_keeps_other_feeds_successful(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            c.write(root/'config/event-supplements.json', {'updated_at':c.iso(NOW),'calendar_events':[]})
            c.write(root/'config/topics.json', c.read(Path(__file__).parents[1]/'config/topics.json'))
            c.write(root/'config/article-overrides.json',{'articles':{},'url_aliases':{}})
            def get(url):
                if url==c.STEAM_NEWS:raise OSError('news unavailable')
                if url==c.STEAM:return {'response':{'result':1,'player_count':123}}
                raise AssertionError('Unexpected URL')
            with patch.object(c,'acquire_snapshots',return_value=([], 'fixture')), patch.object(c,'get_json',side_effect=get):
                self.assertTrue(c.run(root))
            for name in ('players','opportunities','topics'):
                self.assertEqual(c.read(root/f'data/{name}.json')['status'],'ok')
            self.assertEqual(c.read(root/'data/status.json')['errors'],['steam_news: OSError'])

if __name__ == '__main__': unittest.main()
