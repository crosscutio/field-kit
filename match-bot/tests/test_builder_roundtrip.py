"""Config YAML round-trip tests for geocoding/gazetteer form fields."""
from match_bot.gui.builder import form_data_to_yaml, yaml_to_form_data


def test_gazetteer_selection_roundtrips():
    fd = {
        'project_name': 'Geo Test',
        'geo_lat_column': 'lat', 'geo_lon_column': 'lon',
        'gaz_country': 'SWZ', 'gaz_admin1': ['Hhohho', 'Manzini'],
    }
    yaml_str = form_data_to_yaml(fd)
    assert 'gazetteer' in yaml_str and 'SWZ' in yaml_str
    back = yaml_to_form_data(yaml_str)
    assert back['gaz_country'] == 'SWZ'
    assert back['gaz_admin1'] == ['Hhohho', 'Manzini']
    assert back['geo_lat_column'] == 'lat'


def test_matching_tab_yaml_untouched_without_geo_fields():
    fd = {'project_name': 'Plain', 'target_id_column': 'id'}
    yaml_str = form_data_to_yaml(fd)
    assert 'gazetteer' not in yaml_str
    assert 'latitude' not in yaml_str
    back = yaml_to_form_data(yaml_str)
    assert back['gaz_country'] == '' and back['gaz_admin1'] == []
