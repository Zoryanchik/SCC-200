"""Rail XML parsing helpers."""

from xml.etree import ElementTree

from time_utils import seconds_since_midnight, seconds_to_time


def parse_train_services(root: ElementTree.Element):
    """Parse the lt8:trainServices element into a list of service dicts."""
    services = []
    for service in root:
        service_data = {}
        for child in service:
            _, _, tag = child.tag.rpartition("}")
            match tag:
                case "std":
                    service_data["scheduledTime"] = seconds_since_midnight(child.text + ":00")
                case "etd":
                    if child.text == "On time":
                        service_data["status"] = "On time"
                        service_data["departureTime"] = seconds_to_time(service_data["scheduledTime"])
                    elif child.text == "Delayed":
                        service_data["status"] = "Delayed"
                        service_data["departureTime"] = "Unknown Delay"
                    elif child.text == "Cancelled":
                        service_data["status"] = "Cancelled"
                        service_data["departureTime"] = "No Departure"
                    else:
                        etd = seconds_since_midnight(child.text + ":00")
                        delay_min = (etd - service_data["scheduledTime"]) // 60
                        service_data["delayMins"] = delay_min
                        service_data["status"] = f"Delayed {delay_min} mins"
                        service_data["departureTime"] = seconds_to_time(etd)
                case "destination":
                    service_data["destination"] = child[0][0].text
        services.append(service_data)
    return services


def parse_nrcc_messages(root: ElementTree.Element):
    """Parse NRCC disruption messages into frontend alert objects."""
    return [{"message": msg.text, "severity": "warning"} for msg in root]
