from pathlib import Path
import pandas as pd


class ReferenceDataRepository:
    def __init__(self, base_dir: str, allowed_state_to_districts=None):
        root = Path(base_dir)
        self.df_districts = pd.read_csv(root / 'static' / 'data' / 'districts.csv')
        self.df_complaints = pd.read_csv(root / 'static' / 'data' / 'complaints.csv')
        self.df_projects = pd.read_csv(root / 'static' / 'data' / 'projects.csv')

        scoped = allowed_state_to_districts if isinstance(allowed_state_to_districts, dict) else {}
        self.allowed_state_to_districts = {
            str(state): [str(district) for district in districts]
            for state, districts in scoped.items()
            if isinstance(districts, list)
        }

        if self.allowed_state_to_districts:
            allowed_states = set(self.allowed_state_to_districts.keys())
            allowed_districts = {
                district
                for districts in self.allowed_state_to_districts.values()
                for district in districts
            }

            self.df_districts = self.df_districts[
                self.df_districts['state'].isin(allowed_states)
                & self.df_districts['district'].isin(allowed_districts)
            ].copy()

            if 'district' in self.df_complaints.columns:
                self.df_complaints = self.df_complaints[
                    self.df_complaints['district'].isin(allowed_districts)
                ].copy()

            if 'district' in self.df_projects.columns:
                self.df_projects = self.df_projects[
                    self.df_projects['district'].isin(allowed_districts)
                ].copy()

    def get_district_row(self, district_name: str):
        rows = self.df_districts[self.df_districts['district'] == district_name]
        return None if rows.empty else rows.iloc[0]

    def list_states(self):
        available = set(self.df_districts['state'].dropna().unique().tolist())
        if self.allowed_state_to_districts:
            ordered = [state for state in self.allowed_state_to_districts.keys() if state in available]
            return ordered
        return [state for state in self.df_districts['state'].dropna().unique().tolist()]

    def list_districts(self, state=None):
        if state:
            frame = self.df_districts[self.df_districts['state'] == state]
            available = frame['district'].dropna().unique().tolist()
            if self.allowed_state_to_districts:
                ordered = self.allowed_state_to_districts.get(state, [])
                available_set = set(available)
                return [district for district in ordered if district in available_set]
            return available

        available = self.df_districts['district'].dropna().unique().tolist()
        if self.allowed_state_to_districts:
            ordered = [
                district
                for state_name in self.allowed_state_to_districts.keys()
                for district in self.allowed_state_to_districts[state_name]
            ]
            available_set = set(available)
            return [district for district in ordered if district in available_set]
        return available

    def projects_for_district(self, district=None):
        frame = self.df_projects
        if district:
            frame = frame[frame['district'] == district]
        return frame
