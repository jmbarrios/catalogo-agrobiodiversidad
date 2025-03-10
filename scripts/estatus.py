'''
Este script realiza la comparación de lo que hay en nuestra base de datos local
contra lo que hay en Catálogo Conabio (CAT) via Zacatuche.
Si encuentra cambios entre las dos bases, realiza los respectivos cambios
en la base local directamente.
Si por alguna razón tenemos un ID que no se encuentre en Zacatuche
manda un correo a Alicia, Irene y Vivian para dar seguimiento.
Los logs del script se encuentran en logEstatus.txt
'''

import json
import pandas as pd
import requests
import smtplib 
import warnings
warnings.filterwarnings('ignore')

# local import
from paths import *

# credential for login to zendro
auth = {
    "username": username,
    "password": password,
    "client_id": client_id,
    "grant_type": grant_type
}

# make a post to a zendro-keycloak endpoint to retrieve session token
login = requests.post(
    listado_token,
    verify=False,
    data=auth
)

# if status code in the response is 200, then the request was successful and we have
# the session token we need in the login response
if login.status_code == 200:
    
    # we create a session object to use it for the requests to zendro api
    session = requests.Session()

    # and store the token we receive in the 'Authorization' header as a Bearer token
    session.headers.update({
        "Authorization": "Bearer " + login.json()["access_token"]
    })
    
    print("Successful login")

def getInfoTaxon(record_id):
    '''
    Hace una consulta a zacatuche para obtener la información que se necesita de un id.
    Devuelve una pandas.Series con la información solicitada en la query.
    '''
    query = """query taxon{
                taxon(taxonID:"""+"\""+record_id+"\""+"""){
                            id
                            taxonomicStatus
                            scientificName
                            acceptedNameUsage{
                            id
                            scientificName
                            }
                        }
                        }"""
    #print(query)
    # renombrar las columnas
    New_col_names = {'acceptedNameUsage.id': 'id_valido',
                     'acceptedNameUsage.scientificName' : 'taxon_valido',
                     'taxonomicStatus': 'estatus',
                     'scientificName': 'taxon'
                    }

    r = session.post(path_zacatuche, json={'query': query}, verify=False)

    # TO DO: check that response.status = 200, else print error to log

    json_data = json.loads(r.text)
    #print(json_data)
    # case when id is not in CAT
    if json_data['data']['taxon'] is None:
        return None

    # case when there is no id_valido associated to id
    if json_data['data']['taxon']['acceptedNameUsage'] is None:
        df_data = (pd.json_normalize(json_data['data']['taxon'])
                     .rename(columns = New_col_names))
        df_data[['id_valido', 'taxon_valido']] = None, None
        df_data = df_data.fillna('')
        return df_data.loc[0]
    
    df_data = pd.json_normalize(json_data['data']['taxon'])
    df_data = (df_data.rename(columns = New_col_names)
                          .fillna(''))
    
    return df_data.loc[0]


def updateLocal(agrobd_id, New_values):
    '''
    Realiza las modificaciones en la instancia de catalogo-agrobiodiversidad
    de los campos que se le pasen como parámetro.
    '''
    #print("estoy en update: ",agrobd_id)

    New_values['usuario'] = 'Bot validación'
    
    # TO DO: ¿Cómo deben aparecer los valores vacíos en mutation? 
    new_values = ''  
    for field in New_values:
        if New_values[field] is None:
            New_values[field] = ''
        if field == 'es_parientesilvestre' or field == 'es_domesticado' or field == 'es_quelite':
            if New_values[field] == 't':
                New_values[field] = 'true'
            if New_values[field] == 'f':
                New_values[field] = 'false'
            if New_values[field] == "":
                New_values[field] = "null"
            new_values += f'{field}: {New_values[field]} \n'
        else:
            new_values += f'{field}: \"{New_values[field]}\" \n'
        #new_values += f'{field}: \"{New_values[field]}\" \n'

    query = f'''mutation{{
                updateAgrobiodiversidad(
                    id:"{agrobd_id}"
                    {new_values}){{
                    id
                }}
                }}'''
    #print(query)
    session.post(path_siagro, json={'query': query}, verify=False)
    #print(res)


def is_synonym(record):  
    #print("estoy en is_synonym",record['id'])
    return (record['id_valido']) and (record['id'] != record['id_valido'])


def request_agrobd_review(record, check_previous_label=True):
    # if record doesn't have agrobd label, request review
    check = True
    if check_previous_label:
        check = record['categoria_agrobiodiversidad'] != 'Agrobiodiversidad'

    if check:
        try:
            note = record['comentarios_revision'] + ' - REVISAR ETIQUETA AGROBIODIVERSIDAD'

        except KeyError: # case when record not in local agrobd list
            note = 'REVISAR ETIQUETA AGROBIODIVERSIDAD'
        
        updateLocal(record['id'], {'comentarios_revision': note})

        


def delete_agrobd_label(record):
    # TO DO: also delete subcategoria_agrobiodiversidad
    # TO DO: cómo poner campos vacíos    
    updateLocal(record['id'], {'categoria_agrobiodiversidad': '',
                                'es_parientesilvestre': "null",
                                'es_quelite': "null",
                                'es_domesticado': "null"})


def id_is_in_agrobd_list(record_id):
    return record_id in agrobd_list['id'].values


def add_record_to_agrobd_list(record):
    query = f'''mutation{{
                addAgrobiodiversidad(
                    id:"{record['id']}"
                    taxon:\"{record['taxon']}\"
                    estatus:\"{record['estatus']}\"
                    id_valido:"{record['id_valido']}"
                    taxon_valido:\"{record['taxon_valido']}\"
                    usuario:\"Bot validación\"){{
                    id
                }}
                }}'''
    session.post(path_siagro, json={'query': query},verify=False)


def add_agrobd_label(record_id, agrobd):
    ''' 
    Agrega la etiqueta "Agrobiodiversidad" a un registro.
    También agrega la subcategoría y las referencia que hereda del registro 
    cuyo estatus cambió de válido --> sinónimo.
    
    TO DO: Decidir qué hacer en caso de que ya tenga la etiqueta y referencias.

    Recibe:
        record_id (string): nuevo id_valido del registro agrobd
        agrobd (pandas.Series): registro cuyo estatus cambio de válido -> sinónimo
    '''
    updateLocal(record_id, {'categoria_agrobiodiversidad': 'Agrobiodiversidad',
                            'referencia': agrobd['referencia'],
                            'es_parientesilvestre': agrobd['es_parientesilvestre'],
                            'es_domesticado': agrobd['es_domesticado'],
                            'es_quelite': agrobd['es_quelite']
                            })


def sendeMail(string):
    '''
    Envía un correo a las direcciones en "destinatario". 
    Recibe como parámetro un string con los ids a los que se necesita dar seguimiento.
    '''
    remitente = "SIAgro <siagro@siagro.conabio.gob.mx>" 
    #destinatario = ["Vivian <vivbass4@gmail.com>", "Vivian <vbass@conabio.gob.mx>", "Alicia <amastretta@conabio.gob.mx>", "Irene <iramos@conabio.gob.mx>"]
    destinatario = ["Vivian <vbass@conabio.gob.mx>"]
    asunto = "Aviso: no se encontró ID de taxon" 
    mensaje = """´En la validación diaria de registros entre Zacatuche con nuestra base de datos de catalogo-agrobiodiversidad, se detectó que los siguientes IDs no se encontraron en Zacatuche. Favor de dar seguimiento a los casos.

      ID       |      Taxon      

"""+string+"""
    
------------------------------------
Este correo ha sido enviado automáticamente. Favor de no responder.´"""
    email = 'Subject: {}\n\n{}'.format(asunto, mensaje)
    try: 
        smtp = smtplib.SMTP('localhost') 
        smtp.sendmail(remitente, destinatario, email.encode("utf8")) 
        print("Correo enviado")
    except: 
        print("""Error: el mensaje no pudo enviarse. 
        Compruebe que el mensaje no tenga acentos""")


def sync_status_and_agrobd_label(agrobd, catalog):
    '''
    Esta función actualiza los campos de estatus, id_valido, taxon_valido y
    categoria_agrobiodiversidad en la lista local si el registro `agrobd`
    tuvo un cambio de estatus en CAT. Los cambios dependen del tipo de 
    transición que ocurrió para el estatus del registro. Ver detalles en README. 

    Recibe:
        agrobd (pandas.Series): Un registro de la lista local de agrobiodiversidad
        catalog (pandas.Series): Versión de CAT Conabio del registro con mismo id que agrobd
    '''
    # sync estatus, id_valido, taxon_valido
    updateLocal(agrobd['id'], {'estatus': catalog['estatus'],
                               'id_valido': catalog['id_valido'],
                               'taxon_valido': catalog['taxon_valido']
                              })

    # sinonimo --> valido OR NA --> valido
    if agrobd['id'] == catalog['id_valido']:
        request_agrobd_review(agrobd)
    
    # valido --> NA
    elif (agrobd['id'] == agrobd['id_valido']) and (not catalog['id_valido']):
        delete_agrobd_label(agrobd) # TO DO: preguntar a CAT qué hacer en este caso
    
    # sinonimo --> sinonimo
    elif is_synonym(agrobd) and is_synonym(catalog):
        if not id_is_in_agrobd_list(catalog['id_valido']):
            new_record = getInfoTaxon(catalog['id_valido'])
            add_record_to_agrobd_list(new_record)
            request_agrobd_review(new_record, check_previous_label=False)
    
    # valido --> sinonimo
    elif (agrobd['id'] == agrobd['id_valido']) and is_synonym(catalog):
        #print("valido a sinonimo",agrobd['id'])
        delete_agrobd_label(agrobd)
        if not id_is_in_agrobd_list(catalog['id_valido']):
            new_record = getInfoTaxon(catalog['id_valido']) # assumes id exists in CAT
            add_record_to_agrobd_list(new_record)
        # this is the only line that adds the agrobd label to a record
        add_agrobd_label(catalog['id_valido'], agrobd)
    

def sync_agrobd_to_catalog(agrobd_list):
    ''' Pipeline para sincronizar la lista local de agrobiodiversidad
    con el catálogo de Conabio (CAT). El script recorre la lista registro por registro;
    compara la versión actual de la lista `agrobd_list` contra CAT mediante una consulta
    a zacatuche. Si cambió el estatus de un registro en CAT actualiza los campos
    y realiza los cambios necesarios en la categoría agrobiodiversidad.
    Envía un correo para advertir de ids que no están en CAT, pues se trata
    de un error al que se le debe dar seguimiento.
    
    Recibe:
        agrobd_list(pandas.DataFrame): Tabla que contiene la versión más actual
        de la lista local de agrobd. 
    '''
    agroid_not_in_cat = []

    for i, agrobd in agrobd_list.iterrows():
        if 'pendiente' in agrobd['id']:
            continue

        catalog = getInfoTaxon(agrobd['id'])

        # case when agrobd is not in CAT
        if catalog is None:
            agroid_not_in_cat.append((agrobd['id'], agrobd['taxon']))
            continue
        
        # case when there was a change in taxonomic status
        if agrobd['id_valido'] != catalog['id_valido']:
            #print("cambia estatus",agrobd['id_valido'])
            sync_status_and_agrobd_label(agrobd, catalog)

        # case when there was a change in taxonomic name
        if agrobd['taxon'] != catalog['taxon']:
            updateLocal(agrobd['id'], {'taxon': catalog['taxon']})
    
    # format text for email
    # TO DO: add padding to align ids and taxons to fit in a table
    if len(agroid_not_in_cat) != 0:
        email = ''
        for agrobd_id, taxon in agroid_not_in_cat:

            email += f'   {agrobd_id}   |  {taxon} \n'

        sendeMail(email)


if __name__ == '__main__':
    print("Leyendo archivos...")
    agrobd_list = pd.read_csv(path_agrobd_list, keep_default_na=False)    
    print("Comparando archivos...")
    sync_agrobd_to_catalog(agrobd_list)
    print("Termina comparación")