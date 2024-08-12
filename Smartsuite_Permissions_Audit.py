# python3 -m pip install requests
# Version 1.1b - Permissions Export

import requests
import json
import os
from datetime import datetime
from pathlib import Path
import csv

# Secrets
from config import TOKEN, ACCOUNT_ID,DEST_FOLDER
"""
# Generating an API Key - https://help.smartsuite.com/en/articles/4855681-generating-an-api-key
# Create a config.py file and put in it
 TOKEN=dest_folder
 ACCOUNT_ID
"""

# What folder to to write the CSV?
# destFolder="/Users/gadymargalit/backup/"
# destFolder="/temp/backup/"
# dest_folder = "c:\\temp\\backup/"
# destFolder="~/Google Drive/Shared drives/מערך טכנולוגיה/Operations/SmartSuite backup/"
# dest_folder = Path("c:/temp/backup/")
dest_folder = DEST_FOLDER

# Param
base_url = "https://app.smartsuite.com/api/"
tk = "Token " + TOKEN
# the account id you take the first part in the URL https://app.smartsuite.com/ACCOUNTID/solution/SOLUTIONID
headers = {"accept": "application/json", "Authorization": str(tk), "ACCOUNT-ID": ACCOUNT_ID}

# if you want to create it in timestamp folder:
# destFolder=destFolder + datetime.now().strftime("%Y-%m-%d %H:%M:%S") +"/"


def Merge(dict1, dict2): 
    res = dict1 | dict2
    return res 

def get_label_from_slug(data, field_slug):
    for field in data:
        if field['slug'] == field_slug:
            return field['label']
    return None


def get_solutions():
    url_s = base_url + "v1/solutions/"
    resp_s = requests.get(url_s, headers=headers)
    if resp_s.status_code != 200:
        print('error: ' + str(resp_s.status_code))
    else:
        print('Solutions List Loaded Successfully')
        data_s = resp_s.json()
    solutions = {}
    solutions.clear
    for s in data_s:
        solution_info = {
            'name': s['id'],
            'name': s['name'],
            'status': s['status'],
            'permissions': s['permissions'],
        }
        # print(solution_info)
        solutions[s['id']] = solution_info
    return solutions


def get_tables():
    url_applications = base_url + "v1/applications/"
    resp = requests.get(url_applications, headers=headers)
    if resp.status_code != 200:
        print("Can't load Table list error: " + str(resp.status_code))
    else:
        print('Tables List Loaded Successfully')
        tables_data = resp.json()
    tables = {}
    tables.clear
    team = None
    for table in tables_data:
        if table['solution'] in solutions:
            table_info = {
                'name': table['id'],
                'name': table['name'],
                'status': table['status'],
                'solution': solutions[table['solution']]['name'],
                'structure': table['structure'],
                'permissions': table['permissions']
            }
            # print(table_info)
            tables[table['id']] = table_info
            # fixme!
            if team is None and table['solution'] in solutions and solutions[table['solution']]['name'] == 'System' and table['name'] == 'Teams':
                team = table['id']
                print('Table Teamsfound')
    return tables, team

def get_fields_permission():
    field_permissions = []
    for key, item in TN.items():
        table_id=key
        table_name=item['name']
        solution_name=item['solution']
        url_applications = base_url + "v1/applications/" + table_id
        resp = requests.get(url_applications, headers=headers)
        if resp.status_code != 200:
            print("Can't load Table field error: " + str(resp.status_code))
        else:
            print(f'Table: {table_id} fields Loaded Successfully')
            fields_data = resp.json()
            for p in fields_data['field_permissions']:
                print(f"{p}\n")
                #print(fields_data)
                field_info = { 
                    'field_slug': p['field_slug'],
                    'solution': solution_name,
                    'table': table_name,
                    'field': get_label_from_slug(fields_data['structure'], p['field_slug']),
                    'read': p['read'],
                    'write': p['write']
                }
                if field_info['field'] != None:
                    field_permissions.append(field_info)
    return field_permissions

def get_users():
    url_u = base_url + "v1/applications/members/records/list/"
    resp2 = requests.post(url_u, headers=headers)
    if resp2.status_code != 200:
        print('error: ' + str(resp2.status_code))
    else:
        print('Users List Loaded Successfully')
        data_s = resp2.json()
        # respuser=resp2.content.decode('UTF-8')
    users = {}
    users.clear
    for s in data_s['items']:
        if not s['deleted_date']['date'] and s['type'] != '6':  # not deleted and not system account
            user_info = {
                'id': s['id'],
                'full_name': s['full_name']['sys_root'],
                'email': s['email'][0],
                'type': s['type'],
                'role': s['role'],
                'last_login': s['last_login']['date'],
                'locale': s['locale']
            }
            # print(user_info)
            users[s['id']] = user_info
    return users

def get_teams():
    url = base_url + 'v1/applications/' + teams_table + '/records/list/?offet=0'
    params = {"offset": 0}
    response = requests.post(url, params=params, headers=headers)
    if response.status_code == 200:
        print('Teams List Loaded Successfully')
        team_data = response.json()
    else:
        print(f"Request failed with status code {response.status_code}")
        print(response.text)
    teams = {}
    teams.clear
    for team in team_data['items']:
        team_info = {
            'name': team['id'],
            'name': team['name'],
            'status': team['status'],
            'members': team['members']
        }
        # print(team_info)
        teams[team['id']] = team_info
    return teams

def recursive_replace_uid(obj):
    if isinstance(obj, str):
        new_val = obj
        if obj in user_names:
            if user_names[obj]['full_name'] == '':
                new_val = user_names[obj]['email']
            else:
                new_val = user_names[obj]['full_name']
            # print(f"Replacing {obj} with {new_val}")
        if obj in teams_names:
            new_val = teams_names[obj]['name']
            # print(obj, new_val)
        return new_val
    elif isinstance(obj, dict):
        new_dict = {}
        for key, value in obj.items():
            new_dict[key] = recursive_replace_uid(value)
        return new_dict
    elif isinstance(obj, list):
        new_list = []
        for item in obj:
            new_list.append(recursive_replace_uid(item))
        return new_list
    else:
        return obj

# main
solutions = get_solutions()
(tables_names, teams_table) = get_tables()
user_names = get_users()
# print(teamsTable)
teams_names = get_teams()
sol = recursive_replace_uid(solutions)
TN = recursive_replace_uid(tables_names)


field_permissions = get_fields_permission()
field_perm = recursive_replace_uid(field_permissions)
dest_folder.mkdir(parents=True, exist_ok=True)
permissions_file = dest_folder / "permissions.csv"
with permissions_file.open("w", newline='', encoding="utf-8-sig") as file:
    csv_writer = csv.writer(file)
    # Write headers
    csv_writer.writerow(["Type", "Solution", "Table", "Field", "level","members", "teams", "members_read","members_write","teams_read", "teams_write", "owners", "private_to","level_read", "level_write"])
    # Write solutions
    for (k, i) in sol.items():
        type="solution"
        solution=i['name']
        table=""
        field=""
        members_read = ""
        members_write = ""
        teams_read = ""
        teams_write = ""
        members = i['permissions'].get('members', '')
        level = i['permissions'].get('level', '')
        teams = i['permissions'].get('teams', '')
        owners = i['permissions'].get('owners', '')
        private_to = i['permissions'].get('private_to', '')
        level_read = ""
        level_write = ""
        csv_writer.writerow([type, solution, table, field,level, members,teams, members_read,members_write, teams_read, teams_write, owners, private_to])
    # Write tables
    for (k, i) in TN.items():
        #if i['permissions']['level'] != 'all_members':
        type="Table"
        solution=i['solution']
        table=i['name']
        field=""
        level=""
        members_read = ""
        members_write = ""
        teams_read = ""
        teams_write = ""
        members = i['permissions'].get('members', '')    
        teams = i['permissions'].get('teams', '')
        level = i['permissions'].get('level', '')
        permissions_metadata = i['permissions'].get('permissions_metadata', '')
        level_read = ""
        level_write = ""
        csv_writer.writerow([type, solution, table, field,level, members,teams, members_read,members_write, teams_read, teams_write, owners, private_to])
    # Write field permissions
    for fld in field_perm:
        members=""
        level=""
        teams=""
        type="field"
        solution=fld['solution']
        table=fld['table']
        field=fld['field']
        level_read = fld['read']['audience']
        members_read = fld['read'].get('members', '')
        level_write = fld['write']['audience']
        members_write = fld['write'].get('members', '')
        teams_read = fld['read'].get('teams', '')
        teams_write = fld['write'].get('teams', '')
        owners = fld.get('owners', '')
        private_to = fld.get('private_to', '')
        csv_writer.writerow([type, solution, table, field,level, members,teams, members_read,members_write, teams_read, teams_write, owners, private_to,level_read,level_write])
        #print([type, solution, table, field,level, members,teams, members_read,members_write, teams_read, teams_write, owners, private_to,level_read,level_write])
