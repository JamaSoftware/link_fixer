import configparser
import datetime
import getpass
import logging
import json
import os
import sys
import time

from bs4 import BeautifulSoup
import urllib.parse as urlparse

from halo import Halo
from progress.bar import ChargingBar

from py_jama_rest_client.client import JamaClient, APIException

def init_jama_client():
    # do we have credentials in the config?
    credentials_dict = {}
    if 'CREDENTIALS' in config:
        credentials_dict = config['CREDENTIALS']
    try:
        instance_url = get_instance_url(credentials_dict)
        oauth = get_oauth(credentials_dict)
        username = get_username(credentials_dict)
        password = get_password(credentials_dict)
        jama_client = JamaClient(instance_url, credentials=(username, password), oauth=oauth)
        jama_client.get_available_endpoints()
        return jama_client
    except APIException:
        # we cant do things without the API so lets kick out of the execution.
        print('Error: invalid Jama credentials, check they are valid in the config.ini file.')
    except:
        print('Failed to authenticate to <' + get_instance_url(credentials_dict) + '>')

    response = input('\nWould you like to manually enter server credentials?\n')
    response = response.lower()
    if response == 'y' or response == 'yes' or response == 'true':
        config['CREDENTIALS'] = {}
        return init_jama_client()
    else:
        sys.exit()


def get_instance_url(credentials_object):
    if 'instance url' in credentials_object:
        instance_url = str(credentials_object['instance url'])
        instance_url = instance_url.lower()
        # ends with a slash? lets remove this
        if instance_url.endswith('/'):
            instance_url = instance_url[:-1]
        # user forget to put the "https://" bit?
        if not instance_url.startswith('https://') or instance_url.startswith('http://'):
            # if forgotten then ASSuME that this is an https server.
            instance_url = 'https://' + instance_url
        # also allow for shorthand cloud instances
        if '.' not in instance_url:
            instance_url = instance_url + '.jamacloud.com'
        return instance_url
    # otherwise the user did not specify this in the config. prompt the user for it now
    else:
        instance_url = input('Enter the Jama Instance URL:\n')
        credentials_object['instance url'] = instance_url
        return get_instance_url(credentials_object)


def get_username(credentials_object):
    if 'username' in credentials_object:
        username = str(credentials_object['username'])
        return username.strip()
    else:
        username = input('Enter the username (basic auth) or client ID (oAuth):\n')
        credentials_object['username'] = username
        return get_username(credentials_object)


def get_password(credentials_object):
    if 'password' in credentials_object:
        password = str(credentials_object['password'])
        return password.strip()
    else:
        password = getpass.getpass(prompt='Enter your password (basic auth) or client secret (oAuth):\n')
        credentials_object['password'] = password
        return get_password(credentials_object)


def get_oauth(credentials_object):
    if 'using oauth' in credentials_object:
        # this is user input here so lets be extra careful
        user_input = credentials_object['using oauth'].lower()
        user_input = user_input.strip()
        return user_input == 'true' or user_input == 'yes' or user_input == 'y'
    else:
        oauth = input('Using oAuth to authenticate?\n')
        credentials_object['using oauth'] = oauth
        return get_oauth(credentials_object)


def get_project_id():
    try:
        return int(config['PARAMETERS']['project id'])
    except:
        print("missing project id... please provide a project id in the config ini")
        sys.exit()


def init_logger():
    # Setup logging
    try:
        os.makedirs('logs')
    except FileExistsError:
        pass

    current_date_time = datetime.datetime.now().strftime('%m-%d-%Y_%H-%M-%S')
    log_file = 'logs/' + str(current_date_time) + '.log'

    logging.basicConfig(filename=log_file, level=logging.INFO,
                        format='%(asctime)s - %(levelname)s - %(message)s',
                        datefmt='%H:%M:%S')

    logger = logging.getLogger()
    logger.addHandler(logging.StreamHandler(sys.stdout))
    return logger


def get_synced_item(item_id, project_id):
    old_item = client.get_item(item_id)
    synced_items = client.get_items_synceditems(item_id)
    valid_synced_items = []

    if old_item is None or old_item is {}:
        logger.error('Unable to find original item ID:[' + item_id + ']')
        return None

    if synced_items is None or len(synced_items) is 0:
        logger.error('Unable to find new synced items for original item with ID:[' + item_id + ']')

    for synced_item in synced_items:
        if synced_item['project'] is project_id:
            valid_synced_items.append(synced_item['id'])

    # 🤞 only should have one valid synced item here
    if len(valid_synced_items) is 1:
        return valid_synced_items[0]
    elif len(valid_synced_items) > 1:
        logger.error('Multiple synced items found item with ID:[' + item_id + ']')

    # else report some errors here
    logger.error('Unable to find new synced item location for item: [' + item_id + ']')
    return None

# link fixer script, will identify broken links from old projects, and correct the links
# a link to the past
if __name__ == '__main__':
    # int some logging ish
    logger = init_logger()
    start_time = time.time()

    logger.info('Running link fixer script')

    config = configparser.ConfigParser()
    config.read('config.ini')
    logger.info('Reading in configuration file')

    client = init_jama_client()
    instance_url = get_instance_url(config['CREDENTIALS'])
    logger.info('Successfully connected to instance: <' + instance_url + '>')

    # grab the required script parameters from the config.ini file.
    project_id = get_project_id()

    # extra data needed for processing
    valid_project_ids = set()

    """
    STEP ZERO - get all the needed meta data to do this work
    """
    spinner_message = 'Retrieving required meta data from instance...'
    spinner = Halo(text=spinner_message, spinner='dots')
    spinner.start()
    project_list = client.get_projects()
    for project in project_list:
        valid_project_id = project.get('id')
        valid_project_ids.add(valid_project_id)
    spinner.stop()

    """
    STEP ONE - get all items from project    
    """
    # lets validate the project id here before continuing
    if project_id not in valid_project_ids:
        logger.error('Invalid project id provided in the config.ini')
        sys.exit()

    spinner_message = 'Retrieving all items from project ID:[' + str(project_id) + ']'
    spinner = Halo(text=spinner_message, spinner='dots')
    spinner.start()
    items = client.get_items(project_id)
    spinner.stop()
    print('Retrieving ' + str(len(items)) + ' items from project ID:[' + str(project_id) + ']')

    """
    STEP TWO - iterate over all the retrieved items and find bad links   
    """
    broken_link_map = {}
    for item in items:
        item_id = item.get('id')
        fields = item.get('fields')
        for key in fields:
            original_value = fields[key]
            value = fields[key]
            soup = BeautifulSoup(str(value), 'html.parser')
            hyperlinks = soup.find_all('a')
            bad_link_found = False
            bad_link_count = 0

            # iterate over all the hyperlinks
            for hyperlink in hyperlinks:
                href = hyperlink.get('href')
                parsed_link = urlparse.urlparse(href)

                # we only want to process jama links. lets skip over all the other links
                if parsed_link.hostname not in instance_url:
                    continue

                linked_project_id = urlparse.parse_qs(parsed_link.query)['projectId'][0]
                linked_item_id = urlparse.parse_qs(parsed_link.query)['docId'][0]

                # does this project id param not match the current project?
                # if so then this is a bad link
                # there could potentially be more than one bad link per field value. so
                # lets keep track of that.
                if int(linked_project_id) != int(project_id):
                    bad_link_found = True
                    bad_link_count += 1

                    # we will need to get the new item id here
                    corrected_item_id = get_synced_item(linked_item_id, project_id)

                    if corrected_item_id is not None:
                        value = value.replace('?projectId=' + str(linked_project_id), '?projectId=' + str(project_id))
                        value = value.replace(';docId=' + str(linked_item_id), ';docId=' + str(corrected_item_id))

            # we have a bad link for this item?
            if bad_link_found:
                # lets build out an object of all the data we car about for patching and logging
                broken_link_data = {
                    'fieldName': key,
                    'newValue': value,
                    'oldValue': original_value,
                    'counter': str(bad_link_count)
                }
                broken_list = broken_link_map.get(item_id)
                if broken_list is None:
                    broken_list = [broken_link_data]
                else:
                    broken_list.append(broken_link_data)
                broken_link_map[item_id] = broken_list

    """
    STEP THREE - fix and log all the things
    """
    # use a progress bar here. this can be a very long running process
    if len(broken_link_map) > 0:
        with ChargingBar('Correcting broken links ', max=len(broken_link_map), suffix='%(percent).1f%% - %(eta)ds') as bar:
            # iterate over the map, and do work
            for item_id, broken_links in broken_link_map.items():

                patch_list = []
                changed_list = []

                logger.info('Found broken link(s) on item ID: [' + str(item_id) + ']')

                for b in broken_links:
                    # log out the old and new rich text values.
                    logger_old_value = b.get('oldValue').replace('\n', '\n\t')
                    logger_new_value = b.get('newValue').replace('\n', '\n\t')
                    logger.info('Field with name [' + b.get('fieldName') + '] contains ' + b.get('counter') + ' broken link(s)')
                    logger.info('old rich text:\n\t' + logger_old_value)
                    logger.info('new rich text:\n\t' + logger_new_value)

                    payload = {
                        'op': 'replace',
                        'path': '/fields/' + b.get('fieldName'),
                        'value': b.get('newValue')
                    }
                    patch_list.append(payload)


                # lets try and patch this data
                try:
                    client.patch_item(item_id, patch_list)
                    logger.info('Successfully patched item [' + str(item_id) + ']')

                except APIException as error:
                    # we goofed this. 🤷‍
                    logger.error('Failed to patched item [' + str(item_id) + ']')
                    logger.error('API exception response: ' + str(error))

                bar.next()
            bar.finish()
            print('fixed ' + str(len(broken_link_map)) + ' attachments')
    else:
        logger.info('There are zero links to be corrected, exiting...')

    # were done here 🎉🎉🎉
    elapsed_time = '%.2f' % (time.time() - start_time)
    print('total execution time: ' + elapsed_time + ' seconds')
