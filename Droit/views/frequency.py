from ..models import ThingFrequency
import datetime


def add_frequency(thing_id, user_id):
    if not user_id:
        return
    thing_obj = ThingFrequency.objects(thing_id=thing_id).first()
    if user_id not in thing_obj.timestamps:
        thing_obj.timestamps[user_id] = []
    thing_obj.timestamps[user_id].append(datetime.datetime.utcnow())
    thing_obj.save()
