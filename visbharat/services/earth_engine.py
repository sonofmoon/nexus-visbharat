"""Observed remote-sensing indices, never synthetic construction completion."""
import os
from datetime import datetime,timezone,timedelta
from .auditor_workbench import number


class EarthEngineClient:
    def __init__(self,service_account='',key_file='',project_id=''):
        self.service_account=service_account or os.environ.get('EARTH_ENGINE_SERVICE_ACCOUNT','')
        self.key_file=key_file or os.environ.get('EARTH_ENGINE_KEY_FILE','') or os.environ.get('GOOGLE_APPLICATION_CREDENTIALS','')
        self.project_id=project_id or os.environ.get('EARTH_ENGINE_PROJECT','') or os.environ.get('GCP_PROJECT_ID','')

    def fetch_earth_engine_signal(self,project_id_or_geo,signal_type='ndwi_ndbi_proxy'):
        if signal_type!='ndwi_ndbi_proxy': raise ValueError('Only ndwi_ndbi_proxy is supported; satellite construction-progress percentages are not validated')
        if not isinstance(project_id_or_geo,dict): raise ValueError('A project and explicit location are required')
        geo=project_id_or_geo.get('geo') or {}
        lat=number(geo.get('lat'),'latitude',-90,90);lon=number(geo.get('lon'),'longitude',-180,180)
        radius=number(geo.get('radius_m',100),'radius in metres',10,1000)
        import ee
        if self.key_file:
            ee.Initialize(credentials=ee.ServiceAccountCredentials(self.service_account,self.key_file),project=self.project_id)
        else: ee.Initialize(project=self.project_id)
        ee.data.setDeadline(20000)
        end=datetime.now(timezone.utc);start=end-timedelta(days=90)
        area=ee.Geometry.Point([lon,lat]).buffer(radius)
        dataset='COPERNICUS/S2_SR_HARMONIZED'
        collection=ee.ImageCollection(dataset).filterBounds(area).filterDate(start.strftime('%Y-%m-%d'),end.strftime('%Y-%m-%d')).filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE',20))
        count=collection.size().getInfo()
        if not count: raise ValueError('No suitable imagery in this location and observation window')
        # Mask cloud/shadow/snow classes; preserve actual image dates and IDs.
        def mask(image):
            scl=image.select('SCL');ok=scl.neq(3).And(scl.neq(8)).And(scl.neq(9)).And(scl.neq(10)).And(scl.neq(11))
            return image.updateMask(ok)
        composite=collection.map(mask).median()
        index=composite.normalizedDifference(['B3','B8']).add(composite.normalizedDifference(['B11','B8'])).rename('index')
        value=index.reduceRegion(reducer=ee.Reducer.mean(),geometry=area,scale=20,maxPixels=1000000).getInfo().get('index')
        if value is None: raise ValueError('No valid pixels after cloud masking')
        acquired=collection.aggregate_max('system:time_start').getInfo()
        return {'project_id':project_id_or_geo.get('project_id'),'signal_type':signal_type,'signal_value':float(value),'signal_unit':'index',
            'source_provider':'google_earth_engine','source_uri':'https://developers.google.com/earth-engine/datasets/catalog/COPERNICUS_S2_SR_HARMONIZED',
            'dataset_id':dataset,'observed_at':datetime.fromtimestamp(acquired/1000,timezone.utc).isoformat(),
            'provenance':{'mode':'observed_remote_sensing_proxy','image_ids':collection.aggregate_array('system:index').getInfo(),
                'images_count':count,'lat':lat,'lon':lon,'radius_m':radius,'window_start':start.isoformat(),'window_end':end.isoformat(),
                'meaning':'NDWI plus NDBI composite index. Not a construction completion percentage or verified service outcome.'}}


def fetch_earth_engine_signal(project_id_or_geo,signal_type='ndwi_ndbi_proxy'):
    return EarthEngineClient().fetch_earth_engine_signal(project_id_or_geo,signal_type)
