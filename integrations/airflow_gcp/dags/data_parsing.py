from airflow import DAG 
from airflow.operators.python_operator import PythonOperator
import datetime
import requests
import time
import json
import datetime
import pandas as pd
import hopsworks

# Parse Weather Data
def get_city_weather_data(
        city_name: str,
        coordinates: list,
        start_date: str,
        end_date: str = None,
        forecast: bool = True
    ):
        """
        Takes city name, coordinates and returns pandas DataFrame with weather data.
        
        Examples of arguments:
            coordinates=(47.755, -122.2806), start_date="2023-01-01"
        """
        start_of_cell = time.time()
        
        if not end_date:
            end_date = start_date
        
        latitude, longitude = coordinates
        
        params = {
            'latitude': latitude,
            'longitude': longitude,
            'hourly': ["temperature_2m",
                    "relativehumidity_2m",
                    "weathercode",
                    "windspeed_10m",
                    "winddirection_10m",
                    ],
            'start_date': start_date,
            'end_date': end_date,
            'timezone': "Europe/London"
        }
        
        if forecast:
            # historical forecast endpoint
            base_url = 'https://api.open-meteo.com/v1/forecast' 
        else:
            # historical observations endpoint
            base_url = 'https://archive-api.open-meteo.com/v1/archive'  
            
        try:
            response = requests.get(base_url, params=params)
        except ConnectionError:
            response = requests.get(base_url, params=params)
        
        response_json = response.json()    
        res_df = pd.DataFrame(response_json["hourly"])
        
        # rename columns
        res_df = res_df.rename(columns={
            "time": "base_time",
            "temperature_2m": "temperature",
            "weathercode": "weather_code",
            "relativehumidity_2m": "relative_humidity",
            "windspeed_10m": "wind_speed",
            "winddirection_10m": "wind_direction"
        })
        
        # change columns order
        res_df = res_df[
            ['base_time',
            'temperature',
            'relative_humidity',
            'weather_code',
            'wind_speed',
            'wind_direction']
        ]
        
        # convert dates in 'date' column
        res_df["base_time"] = pd.to_datetime(res_df["base_time"])
        res_df['city_name'] = city_name
        res_df['forecast_hr'] = 0
        
        end_of_cell = time.time()
        print(f"Parsed weather for {city_name} since {start_date} till {end_date}.")
        print(f"Took {round(end_of_cell - start_of_cell, 2)} sec.\n")
            
        return res_df

def get_weather_data():
    city_coords = {
        "London": [51.51, -0.13],
        "Paris": [48.85, 2.35],
        "Stockholm": [59.33, 18.07],
        "New York": [40.71, -74.01],
        "Los Angeles": [34.05, -118.24],
        "Singapore": [1.36, 103.82],
        "Sydney": [-33.87, 151.21],
        "Hong Kong": [22.28, 114.16],
        "Rome": [41.89, 12.48],
        "Kyiv": [50.45, 30.52]
    }

    # Get today's date
    today = datetime.datetime.today().date().strftime("%Y-%m-%d")

    # Parse and insert updated data from observations endpoint
    parsed_df = pd.DataFrame()

    for city_name, city_coord in city_coords.items():
        weather_df_temp = get_city_weather_data(
            city_name,
            city_coord,
            today,
        )
        parsed_df = pd.concat([parsed_df, weather_df_temp])

    # Perform feature engineering
    parsed_df['index_column'] = parsed_df.index
    parsed_df['hour'] = parsed_df['base_time'].dt.hour
    parsed_df['day'] = parsed_df['base_time'].dt.day
    parsed_df['temperature_diff'] = parsed_df.groupby('city_name')['temperature'].diff()
    parsed_df['wind_speed_category'] = pd.cut(
        parsed_df['wind_speed'],
        bins=[0, 2.5, 5.0, 7.5, float('inf')],
        labels=['Low', 'Moderate', 'High', 'Very High']
    ).astype(str)
    parsed_df["base_time"] = parsed_df["base_time"].astype(int) // 10**9
    parsed_df.fillna(0, inplace=True)

    return parsed_df.to_json(orient='records')


def insert_data(**kwargs):

    # Retrieve the output from the weather_data task
    ti = kwargs['ti']
    weather_data_json = ti.xcom_pull(task_ids='parse_weather_data')

    # Parse the JSON string into a list of dictionaries
    weather_data_list = json.loads(weather_data_json)

    # Convert the list of dictionaries into a Pandas DataFrame
    weather_data = pd.DataFrame(weather_data_list)

    # Your code to insert data into Hopsworks Feature Store using weather_data
    print("Received weather data:", weather_data)

    project = hopsworks.login(
        api_key_value='{YOUR_HOPSWORKS_API_KEY}',
        )

    fs = project.get_feature_store()

    weather_fg = fs.get_or_create_feature_group(
        name="weather_fg",
        version=1,
        description="Weather data",
        primary_key=["city_name", "hour"],
        online_enabled=True,
        event_time="base_time",
    )
    weather_fg.insert(weather_data)

    return 


default_args = {
    'start_date': datetime.datetime(2023, 1, 1),
}

with DAG(
    'Feature_Pipeline',
    description='DAG to Parse, Transform and Insert data into Hopsworks Feature Store',
    default_args=default_args, 
    schedule_interval='@daily', 
    catchup=False,
    ) as dag:

    weather_data = PythonOperator(
        task_id='parse_weather_data',
        python_callable=get_weather_data,
    )

    insertion = PythonOperator(
        task_id='insert_weather_data',
        python_callable=insert_data,
    )

    weather_data >> insertion
