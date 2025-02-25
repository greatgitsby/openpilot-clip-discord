# Parses a route or URL string, throwing an exception if it's invalid.

import dataclasses

from urllib.parse import urlparse

import requests

# Dataclass for a parsed route or URL

@dataclasses.dataclass
class ParsedRouteOrURL:
    route: str
    start_seconds: int
    length_seconds: int

# These have a dash in the 2nd part and 4 parts
# https://connect.comma.ai/a2a0ccea32023010/2023-07-27--13-01-19/7/124
# Notably, instead of looking at routes_segments, look at the route itself
# curl https://api.commadotai.com/v1/route/99c94dc769b5d96e\|2019-05-17--17-31-58/
def parse_route_relative_url(
    route_or_url: str,
) -> ParsedRouteOrURL:
    # Parse the URL
    parsed_url = urlparse(route_or_url)
    # Check the hostname
    if parsed_url.hostname != "connect.comma.ai":
        raise ValueError("Invalid hostname in URL")
    # Check the path
    path_parts = parsed_url.path.split("/")
    # There should be five parts
    if len(path_parts) != 5:
        raise ValueError("Invalid path in URL")
    # The first part should be the dongle ID
    dongle_id = path_parts[1]
    # The second part should be the route name in url slash format
    segment_name = path_parts[2]
    internal_route_name = f"{dongle_id}|{segment_name}"
    # The third part should be the start time relative to the start time of the route
    start_time = int(path_parts[3])
    # The fourth part should be end time relative to the start time of the route
    end_time = int(path_parts[4])


    start_seconds = start_time
    # Compute the length seconds
    length_seconds = (end_time - start_time)

    print(f"Route: {internal_route_name}")
    print(f"Matched Route: {internal_route_name}")
    print(f"Start Seconds: {start_seconds}")
    print(f"Start Time: {start_time}")
    print(f"Length Seconds: {length_seconds}")
    # Return the parsed route
    return ParsedRouteOrURL(internal_route_name, start_seconds, length_seconds)

# These have no dash in the 2nd part and 3 parts
# https://connect.comma.ai/a2a0ccea32023010/1690488131496/1690488151496
def parse_absolute_time_url(
    route_or_url: str,
    start_seconds: int,
    length_seconds: int,
    jwt_token: str = None,
) -> ParsedRouteOrURL:
    # Check if the URL is like this:
    # https://connect.comma.ai/a2a0ccea32023010/1690488084000/1690488085000
    # * Hostname is connect.comma.ai
    # * Path is "dongle id"/"start time"/"end time"
    # * Start time and end time are in milliseconds since the epoch
    # * Start time is before end time

    # Parse the URL
    parsed_url =  urlparse(route_or_url)

    # Check the hostname
    if parsed_url.hostname != "connect.comma.ai":
        raise ValueError("Invalid hostname in URL")

    # Check the path
    path_parts = parsed_url.path.split("/")
    # There should be three parts
    if len(path_parts) != 4:
        raise ValueError("Invalid path in URL")
    # The first part should be the dongle ID
    dongle_id = path_parts[1]
    # The second part should be the start time
    start_time = int(path_parts[2])
    # The third part should be the end time
    end_time = int(path_parts[3])
    # Start time should be before end time
    if start_time >= end_time:
        raise ValueError("Invalid start and end times in URL")

    # The above URL is equivalent to this API call:
    # https://api.comma.ai/v1/devices/a2a0ccea32023010/routes_segments?end=1690488851596&start=1690488081496

    # Make the API call
    api_url = f"https://api.comma.ai/v1/devices/{dongle_id}/routes_segments?end={end_time}&start={start_time}"
    if jwt_token:
        response = requests.get(api_url, headers={"Authorization": f"JWT {jwt_token}"})
    else:
        response = requests.get(api_url)
    # Check the response
    if response.status_code != 200:
        raise ValueError("Invalid API response")

    json = response.json()

    # Response (Excerpt) is like this
    # [
    #   {
    #     "fullname": "a2a0ccea32023010|2023-07-27--13-01-19",
    #     "segment_end_times": [
    #          1690488142995,
    #          1690488203050,
    #          1690488263032,
    #          1690488322998,
    #          1690488383009,
    #          1690488443000,
    #          1690488503010,
    #          1690488563006,
    #          1690488623013,
    #          1690488683016,
    #          1690488743014,
    #          1690488803019,
    #          1690488851596
    #      ],
    #      "segment_numbers": [
    #          0,
    #          1,
    #          2,
    #          3,
    #          4,
    #          5,
    #          6,
    #          7,
    #          8,
    #          9,
    #          10,
    #          11,
    #          12
    #      ],
    #      "segment_start_times": [
    #          1690488081496,
    #          1690488143038,
    #          1690488203035,
    #          1690488263028,
    #          1690488323037,
    #          1690488383025,
    #          1690488443035,
    #          1690488503030,
    #          1690488563038,
    #          1690488623040,
    #          1690488683035,
    #          1690488743039,
    #          1690488803035
    #      ],
    #   }
    # ]
    # And keep in mind there can be multiple unrelated routes in the response.
    # It seems filtering does not work and it returns unrelated routes.
    # Try to find the route of interest
    # As an example, https://connect.comma.ai/a2a0ccea32023010/1690488152777/1690488186013
    # Should return
    # Route: a2a0ccea32023010|2023-07-27--13-01-19
    # Start Seconds: 71
    # Length Seconds: 104
    # Ignore all the milliseconds too

    # Discover what the start and end times of each route returned are
    matched_route = None

    for route_info in json:
        start_in_route = False
        end_in_route = False
        route_start_time = route_info["start_time_utc_millis"]
        route_end_time = route_info["end_time_utc_millis"]
        # Check if the start time is in the route
        if start_time >= route_start_time and start_time <= route_end_time:
            start_in_route = True
        # Check if the end time is in the route
        if end_time >= route_start_time and end_time <= route_end_time:
            end_in_route = True
        # If both the start and end times are in the route, we found our match
        if start_in_route and end_in_route:
            matched_route = route_info
            break

    # If we didn't find a match, throw an exception
    if matched_route is None:
        if jwt_token:
            raise ValueError(f"Route not found from URL and JWT Token. Make sure you're using a correct JWT token of 181+ characters from https://jwt.comma.ai .")
        else:
            raise ValueError(f"Route not found from URL. Route is possibly not set to Public. Visit the URL {route_or_url} and make sure Public is toggled under the \"More Info\" drop-down. You can always make it not Public after you're done rendering a clip.")

    # Get the route name
    route_name = matched_route["fullname"]
    # Compute the start seconds

    start_seconds = (start_time - route_start_time) // 1000
    # Compute the length seconds
    length_seconds = (end_time - start_time) // 1000

    print(f"Route: {route_name}")
    print(f"Matched Route: {matched_route}")
    print(f"Start Seconds: {start_seconds}")
    print(f"Start Time: {start_time}")
    print(f'Route Start Time: {route_start_time}')
    print(f"Length Seconds: {length_seconds}")
    # Return the parsed route
    return ParsedRouteOrURL(route_name, start_seconds, length_seconds)


def parse_route_or_url(
    route_or_url: str,
    start_seconds: int,
    length_seconds: int,
    jwt_token: str = None,
) -> ParsedRouteOrURL:
    # if the route_or_url is a route, just return it
    # Assume that a route is a string with a pipe in it
    if "|" in route_or_url:
        return ParsedRouteOrURL(route_or_url, start_seconds, length_seconds)

    # Check if the URL is an absolute time URL
    # Parse URL and check if it's a valid URL
    parsed_url = urlparse(route_or_url)
    url_parts = parsed_url.path.split("/")

    # Check if the host is connect.comma.ai
    valid_hostname = parsed_url.hostname == "connect.comma.ai"
    if not valid_hostname:
        raise ValueError("Invalid hostname in URL")
    if not parsed_url.hostname == "connect.comma.ai":
        raise ValueError("Invalid hostname in URL")

    # Check if the path has 3 parts
    if len(url_parts) == 4 and "-" not in url_parts[2]:
        return parse_absolute_time_url(route_or_url, start_seconds, length_seconds, jwt_token)
    # Check if the path has 4 parts and a dash in the 2nd part
    if len(url_parts) == 5 and "-" in url_parts[2]:
        return parse_route_relative_url(route_or_url)

    print("Number of url parts: ", len(url_parts))

    # If the URL is not an absolute time URL, throw an exception
    raise ValueError("Invalid URL")

# Make an argparse test for this
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="parse a route or URL")
    parser.add_argument("route_or_url", type=str, help="Route or URL to parse")
    parser.add_argument("start_seconds", type=int, help="Start time in seconds")
    parser.add_argument(
        "length_seconds", type=int, help="Length of the segment to render"
    )
    parser.add_argument(
        "--jwt_token",
        type=str,
        help="JWT token to use for API calls (optional)",
        default=None,
    )
    args = parser.parse_args()

    parsed_route = parse_route_or_url(
        args.route_or_url,
        args.start_seconds,
        args.length_seconds,
        args.jwt_token,
    )

    print(f"Route: {parsed_route.route}")
    print(f"Start Seconds: {parsed_route.start_seconds}")
    print(f"Length Seconds: {parsed_route.length_seconds}")
