from configparser import ConfigParser
from xmlrpc import client as xmlrpclib
import yaml


class odoo_xmlrcp_migration(object):
    socks = {}
    plan = ['base']
    domain = []
    cache = {'plans': {}, 'external_ids': {}}

    def __init__(self, config_file='/etc/odoo_xmlrcp_migration.conf'):
        self.config = ConfigParser()
        self.config.read(config_file)
        self.data_dir = self.config.get("yalm", 'path')
        self.socks['from'] = {
            'dbname': self.config.get("odooserver_1", 'dbname'),
            'username': self.config.get("odooserver_1", 'username'),
            'pwd': self.config.get("odooserver_1", 'pwd'),
            'url': self.config.get("odooserver_1", 'url'),
        }
        self.socks['from']['sock_common'] = xmlrpclib.ServerProxy(self.socks['from']['url'] + 'xmlrpc/common')

        self.socks['from']['uid'] = self.socks['from']['sock_common'].login(
            self.socks['from']['dbname'],
            self.socks['from']['username'],
            self.socks['from']['pwd']
        )
        self.socks['from']['sock'] = xmlrpclib.ServerProxy(self.socks['from']['url'] + 'xmlrpc/object')

        self.socks['to'] = {
            'dbname': self.config.get("odooserver_2", 'dbname'),
            'username': self.config.get("odooserver_2", 'username'),
            'pwd': self.config.get("odooserver_2", 'pwd'),
            'url': self.config.get("odooserver_2", 'url'),
        }
        self.socks['to']['sock_common'] = xmlrpclib.ServerProxy(self.socks['to']['url'] + 'xmlrpc/common')

        self.socks['to']['uid'] = self.socks['to']['sock_common'].login(
            self.socks['to']['dbname'],
            self.socks['to']['username'],
            self.socks['to']['pwd']
        )
        self.socks['to']['sock'] = xmlrpclib.ServerProxy(self.socks['to']['url'] + 'xmlrpc/object')

    def clean_cache(self):
        self.cache = {'plans': {}, 'external_ids': {}}

    def fields_get(self, server, model):
        server = self.socks[server]
        sock = server['sock']
        return sock.execute(server['dbname'], server['uid'], server['pwd'], model, 'fields_get')

    def compare_model(self, model_from, model_to=False):
        fields = {}
        model_to = model_to if model_to else model_from
        fields_from = self.fields_get('from', model_from)
        fields_to = self.fields_get('to', model_to)
        keys_from = set(fields_from.keys())
        keys_to = set(fields_to.keys())
        intersection = keys_from & keys_to
        for field in list(intersection):
            if fields_to[field]['store'] is False:
                continue
            field_temp = {}
            field_temp['from'] = {'name': field, 'type': fields_from[field]['type']}
            if 'relation' in fields_from[field]:
                field_temp['from']['relation'] = fields_from[field]['relation']
            field_temp['to'] = {'name': field, 'type': fields_to[field]['type']}
            if 'relation' in fields_to[field]:
                field_temp['to']['relation'] = fields_from[field]['relation']
            if fields_from[field]['type'] == fields_from[field]['type']:
                field_temp['map_method'] = 'magic_map'
            else:
                field_temp['map_method'] = '%s2%s' % (fields_from[field]['type'] == fields_from[field]['type'])

            fields[field] = field_temp
        return fields

    def save_plan(self, model_from, model_to=False, plan=False):
        model_to = model_to if model_to else model_from
        if not plan:
            plan = self.plan[0]
        data = {}
        data['model_from'] = model_from
        data['model_to'] = model_to
        data['domain'] = []
        data['external_id_nomenclature'] = model_from.replace('.', '_') + "_%s"
        data['external_id_method'] = 'row_get_id'
        data['fields'] = self.compare_model(model_from, model_to)
        model_name = model_from.replace('.', '_')
        with open('%s/%s_%s.yaml' % (self.data_dir, plan, model_name), 'w') as file:
            yaml.dump(data, file)

    def load_plan(self, model_from):
        model_name = model_from.replace('.', '_')
        if model_name in self.cache['plans']:
            return self.cache['plans'][model_name]
        result = {}

        for plan in self.plan:
            with open('%s/%s_%s.yaml' % (self.data_dir, plan, model_name)) as file:
                data = yaml.full_load(file)
                if not len(result):
                    result = data
                else:
                    result['fields'] += data['fields']
                    result['domain'] += data['domain']
        if len(result) == 0:
            raise "Not exists plan for %s" % model_from
        self.cache['plans'][model_name] = result
        return result

    def migrate(self, model_name, row_ids=False):
        plan = self.load_plan(model_name)
        if not row_ids:
            row_ids = self.get_ids(plan['model_from'], plan['domain'] + self.domain)
        n = 100
        chunk = [row_ids[i:i + n] for i in xrange(0, len(row_ids), n)]
        for ids in chunk:
            rows = self.read(plan['model_from'], ids, plan['fields'].keys())
            for row in rows:
                data = self.map_data(plan, row)
                self.save(plan, data, row['id'])

            break

    def save(self, plan, values, orig_id):
        external_id_method = getattr(self, plan['external_id_method'])
        ext_id = external_id_method(plan['model_to'], orig_id, plan['external_id_nomenclature'])
        server = self.socks['to']
        sock = server['sock']
        if len(ext_id):
            print ('update')
            sock.execute(
                server['dbname'],
                server['uid'],
                server['pwd'],
                plan['model_to'],
                'write',
                [ext_id[0]['res_id']],
                values
            )
        else:
            print ('create')

            res_id = sock.execute(
                server['dbname'],
                server['uid'],
                server['pwd'],
                plan['model_to'],
                'create',
                [values]
            )
            self.add_external_id(plan['model_to'], orig_id, res_id[0], plan['external_id_nomenclature'])

    def get_ids(self, model, domain):
        server = self.socks['from']
        sock = server['sock']
        return sock.execute(server['dbname'], server['uid'], server['pwd'], model, 'search', domain)

    def read(self, model, ids, fields):
        server = self.socks['from']
        sock = server['sock']
        return sock.execute(
            server['dbname'],
            server['uid'],
            server['pwd'],
            model,
            'read',
            ids,
            fields
        )

    def map_data(self, plan, row):
        maping = {}
        for field in plan['fields']:
            f = plan['fields'][field]
            map_method = getattr(self, f['map_method'] if 'map_method' in f else 'magic_map')
            val = map_method(row[f['from']['name']], field, plan)
            if val is not None:
                maping[f['to']['name']] = map_method(row[f['from']['name']], field, plan)
        return maping

    def magic_map(self, value, field, plan):
        field_data = plan['fields'][field]
        if field_data['from']['type'] in ['char', 'float', 'integer', 'text', 'html', 'boolean']:
            return value
        elif field_data['from']['type'] == 'one2many':
            subplan = self.load_plan(field_data['from']['relation'])
            external_id_method = getattr(self, subplan['external_id_method'])
            ext_id = external_id_method(subplan['model_to'], value[0], subplan['external_id_nomenclature'])
            if len(ext_id):
                return ext_id[0]['res_id']
            else:
                new = self.migrate(
                    field_data['from']['relation'],
                    [value[0]]
                )
        elif field_data['from']['type'] in ['many2one']:
            pass
        elif field_data['from']['type'] in ['many2many']:
            pass

        return None

    def row_get_id(self, model, value, nomeclature):
        server = self.socks['to']
        sock = server['sock']
        args = [('name', '=', nomeclature % value),
                ('module', '=', 'xmlrpc_migration'),
                ('model', '=', model)]
        return sock.execute(
            server['dbname'],
            server['uid'],
            server['pwd'],
            'ir.model.data',
            'search_read',
            args,
            ['res_id']
        )

    def add_external_id(self, model, orig_id, dest_id, nomeclature):
        server = self.socks['to']
        sock = server['sock']
        vals = [{
                'name': nomeclature % orig_id,
                'module': 'xmlrpc_migration',
                'model': model,
                'res_id': dest_id,
                }]

        return sock.execute(
            server['dbname'],
            server['uid'],
            server['pwd'],
            'ir.model.data',
            'create',
            vals
        )
