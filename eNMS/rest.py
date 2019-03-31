from datetime import datetime
from flask import current_app, Flask, jsonify, make_response, request
from flask_restful import Api, Resource
from flask.wrappers import Response
from logging import info
from psutil import cpu_percent
from uuid import getnode
from typing import Union

from eNMS.extensions import auth, scheduler
from eNMS.admin.functions import migrate_export, migrate_import
from eNMS.automation.functions import scheduler_job
from eNMS.base.functions import delete, factory, fetch
from eNMS.inventory.functions import object_export, object_import


@auth.get_password
def get_password(username: str) -> str:
    return getattr(fetch("User", name=username), "password", False)


@auth.error_handler
def unauthorized() -> Response:
    return make_response(jsonify({"message": "Unauthorized access"}), 403)


class Heartbeat(Resource):
    def get(self) -> dict:
        return {
            "name": getnode(),
            "cluster_id": current_app.config["CLUSTER_ID"],
            "cpu_load": cpu_percent(),
        }


class RestAutomation(Resource):
    decorators = [auth.login_required]

    def post(self) -> Union[str, dict]:
        data = request.get_json()
        job = fetch("Job", name=data["name"])
        payload = data["payload"]
        handle_asynchronously = data.get("async", False)
        try:
            targets = {
                fetch("Device", name=device_name)
                for device_name in data.get("devices", "")
            } | {
                fetch("Device", ip_address=ip_address)
                for ip_address in data.get("ip_addresses", "")
            }
            for pool_name in data.get("pools", ""):
                targets |= {d for d in fetch("Pool", name=pool_name).devices}
        except Exception as e:
            info(f"REST API run_job endpoint failed ({str(e)})")
            return str(e)
        if handle_asynchronously:
            scheduler.add_job(
                id=str(datetime.now()),
                func=scheduler_job,
                run_date=datetime.now(),
                args=[job.id, None, [d.id for d in targets], payload],
                trigger="date",
            )
            return job.serialized
        else:
            return job.try_run(targets=targets, payload=payload)[0]


class GetInstance(Resource):
    decorators = [auth.login_required]

    def get(self, cls: str, name: str) -> dict:
        return fetch(cls, name=name).serialized

    def delete(self, cls: str, name: str) -> dict:
        return delete(fetch(cls, name=name))


class GetConfiguration(Resource):
    decorators = [auth.login_required]

    def get(self, name: str) -> str:
        device = fetch("Device", name=name)
        return device.configurations[max(device.configurations)]


class UpdateInstance(Resource):
    decorators = [auth.login_required]

    def post(self, cls: str) -> dict:
        return factory(cls, **request.get_json()).serialized


class Migrate(Resource):
    decorators = [auth.login_required]

    def post(self, direction: str) -> Union[bool, str]:
        args = (current_app, request.get_json())
        return migrate_import(*args) if direction == "import" else migrate_export(*args)


class Topology(Resource):
    decorators = [auth.login_required]

    def post(self, direction: str) -> Union[bool, str]:
        if direction == "import":
            data = request.form.to_dict()
            for property in ("replace", "update_pools"):
                data[property] = True if data[property] == "True" else False
            return object_import(data, request.files["file"])
        else:
            return object_export(request.get_json(), current_app.path)


def configure_rest_api(app: Flask) -> None:
    api = Api(app)
    api.add_resource(Heartbeat, "/rest/is_alive")
    api.add_resource(RestAutomation, "/rest/run_job")
    api.add_resource(UpdateInstance, "/rest/instance/<string:cls>")
    api.add_resource(GetInstance, "/rest/instance/<string:cls>/<string:name>")
    api.add_resource(GetConfiguration, "/rest/configuration/<string:name>")
    api.add_resource(Migrate, "/rest/migrate/<string:direction>")
    api.add_resource(Topology, "/rest/topology/<string:direction>")
