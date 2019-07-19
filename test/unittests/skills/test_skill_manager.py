import tempfile
from os import makedirs, path
from shutil import rmtree
from time import time
from unittest import TestCase
from unittest.mock import Mock, patch, PropertyMock

from pathlib import Path

from mycroft.skills.skill_manager import _get_last_modified_date, SkillManager

MOCK_PACKAGE = 'mycroft.skills.skill_manager.'


class MockMessageBus:
    def __init__(self):
        self.message_types = []
        self.message_data = []
        self.event_handlers = []

    def emit(self, message):
        self.message_types.append(message.type)
        self.message_data.append(message.data)

    def on(self, event, _):
        self.event_handlers.append(event)


class MycroftSkillTest(TestCase):
    def setUp(self):
        self.message_bus = MockMessageBus()
        self.temp_dir = tempfile.mkdtemp()
        self._mock_msm()
        self._mock_config()

    def _mock_msm(self):
        msm_patch = patch(MOCK_PACKAGE + 'msm_creator')
        self.create_msm_mock = msm_patch.start()
        msm_mock = Mock()
        msm_mock.skills_dir = self.temp_dir
        self.create_msm_mock.return_value = msm_mock
        self.addCleanup(msm_patch.stop)

    def _mock_config(self):
        config_mgr_patch = patch(MOCK_PACKAGE + 'Configuration')
        self.config_mgr_mock = config_mgr_patch.start()
        get_config_mock = Mock()
        get_config_mock.return_value = self._build_config()
        self.config_mgr_mock.get = get_config_mock
        self.addCleanup(config_mgr_patch.stop)

    def _build_config(self):
        config = dict(
            skills=dict(
                msm=dict(
                    directory='skills',
                    versioned=True,
                    repo=dict(
                        cache='.skills-repo',
                        url='https://github.com/MycroftAI/mycroft-skills',
                        branch='19.02'
                    )
                ),
                update_interval=1.0,
                auto_update=False,
                blacklisted_skills=[],
                priority_skills=['foobar'],
                upload_skill_manifest=True
            ),
            data_dir=self.temp_dir
        )

        return config

    def tearDown(self):
        rmtree(self.temp_dir)

    def test_get_last_modified_date(self):
        for file_name in ('foo.txt', 'bar.py', '.foobar', 'bar.pyc'):
            file_path = path.join(self.temp_dir, file_name)
            Path(file_path).touch()
        last_modified_date = _get_last_modified_date(self.temp_dir)
        expected_result = path.getmtime(path.join(self.temp_dir, 'bar.py'))
        self.assertEqual(last_modified_date, expected_result)

    def test_instantiate(self):
        sm = SkillManager(self.message_bus)
        self.assertEqual(sm.config['data_dir'], self.temp_dir)
        self.assertEqual(sm.update_interval, 3600)
        self.assertEqual(sm.dot_msm, path.join(self.temp_dir, '.msm'))
        self.assertFalse(path.exists(sm.dot_msm))
        self.assertIsNone(sm.last_download)
        self.assertLess(sm.next_download, time())
        expected_result = [
            'skill.converse.request',
            'mycroft.internet.connected',
            'skillmanager.update',
            'skillmanager.list',
            'skillmanager.deactivate',
            'skillmanager.keep',
            'skillmanager.activate',
            'mycroft.paired'
        ]
        self.assertListEqual(expected_result, self.message_bus.event_handlers)

    def test_load_installed_skills(self):
        skill_file_path = path.join(self.temp_dir, '.mycroft_skills')
        with open(skill_file_path, 'w') as skill_file:
            skill_file.write('FooSkill\n')
            skill_file.write('BarSkill\n')

        patch_path = MOCK_PACKAGE + 'SkillManager.installed_skills_file'
        with patch(patch_path, new_callable=PropertyMock) as mock_file:
            mock_file.return_value = skill_file_path
            skills = SkillManager(self.message_bus).load_installed_skills()

        self.assertEqual({'FooSkill', 'BarSkill'}, skills)

    def test_save_installed_skills(self):
        skill_file_path = path.join(self.temp_dir, '.mycroft_skills')
        installed_skills = ['FooSkill', 'BarSkill']
        patch_path = MOCK_PACKAGE + 'SkillManager.installed_skills_file'
        with patch(patch_path, new_callable=PropertyMock) as mock_file:
            mock_file.return_value = skill_file_path
            SkillManager(self.message_bus).save_installed_skills(
                installed_skills
            )

        with open(skill_file_path) as skill_file:
            skills = skill_file.readlines()

        self.assertListEqual(['FooSkill\n', 'BarSkill\n'], skills)

    def test_post_manifest_allowed(self):
        msm = Mock()
        msm.skills_data = 'foo'
        with patch(MOCK_PACKAGE + 'is_paired') as paired_mock:
            paired_mock.return_value = True
            with patch(MOCK_PACKAGE + 'DeviceApi', spec=True) as api_mock:
                SkillManager(self.message_bus).post_manifest(msm)
                api_instance = api_mock.return_value
                api_instance.upload_skills_data.assert_called_once_with('foo')
            paired_mock.assert_called_once()

    def test_remove_git_locks(self):
        git_dir = path.join(self.temp_dir, 'foo/.git')
        git_lock_file_path = path.join(git_dir, 'index.lock')
        makedirs(git_dir)
        with open(git_lock_file_path, 'w') as git_lock_file:
            git_lock_file.write('foo')

        SkillManager(self.message_bus).remove_git_locks()

        self.assertFalse(path.exists(git_lock_file_path))

    def test_load_priority(self):
        sm = SkillManager(self.message_bus)
        sm._load_or_reload_skill = Mock()
        skill, sm.msm.list = self._build_mock_msm_skill_list()
        sm.load_priority()

        self.assertFalse(skill.install.called)
        sm._load_or_reload_skill.assert_called_once_with(skill.path)

    def test_install_priority(self):
        sm = SkillManager(self.message_bus)
        sm._load_or_reload_skill = Mock()
        skill, sm.msm.list = self._build_mock_msm_skill_list()
        skill.is_local = False
        sm.load_priority()

        self.assertTrue(skill.install.called)
        sm._load_or_reload_skill.assert_called_once_with(skill.path)

    def test_priority_skill_not_recognized(self):
        sm = SkillManager(self.message_bus)
        sm._load_or_reload_skill = Mock()
        skill, sm.msm.list = self._build_mock_msm_skill_list()
        skill.name = 'barfoo'
        sm.load_priority()

        self.assertFalse(skill.install.called)
        self.assertFalse(sm._load_or_reload_skill.called)

    def test_priority_skill_install_failed(self):
        sm = SkillManager(self.message_bus)
        sm._load_or_reload_skill = Mock()
        skill, sm.msm.list = self._build_mock_msm_skill_list()
        skill.is_local = False
        skill.install.side_effect = ValueError
        sm.load_priority()

        self.assertRaises(ValueError, skill.install)
        self.assertFalse(sm._load_or_reload_skill.called)

    def _build_mock_msm_skill_list(self):
        skill = Mock()
        skill.name = 'foobar'
        skill.is_local = True
        skill.install = Mock()
        skill.path = path.join(self.temp_dir, 'foobar')
        skill_list_func = Mock()
        skill_list_func.return_value = [skill]

        return skill, skill_list_func
