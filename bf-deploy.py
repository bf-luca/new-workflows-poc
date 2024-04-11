#!/bin/python3
import os
import yaml
import fnmatch
import git
from shutil import which
import sys
import subprocess
from subprocess import CompletedProcess
import argparse
import re
from typing import Final, Any
from yaml.constructor import SafeConstructor
from setuptools import setup, find_packages

REQUIREMENTS_FILE: Final[str] = "requirements.txt"
WORKFLOWS_FOLDER: Final[str] = ".github/workflows"
CONSECUTIVE_ENVS: Final[int] = 16
AVAILABLE_ENVS: Final[set[str]] = [
    f"{i:02d}" for i in range(1, CONSECUTIVE_ENVS + 1)
] + [
    "20",
    "oms",
    "staging",
]
AVAILABLE_ENVS_SHORT: Final[str] = "01-16|20|oms|staging"
YES_STRINGS: Final[list[str]] = ["y", "yes", "t", "true", "1"]
NO_STRINGS: Final[list[str]] = ["n", "no", "f", "false", "0"]

repo = git.Repo(search_parent_directories=True)


def init_bfd() -> None:
    if os.path.exists(REQUIREMENTS_FILE):
        subprocess.run(["pip", "install", "-r", "requirements.txt"])
    else:
        print(
            f"The file '{REQUIREMENTS_FILE}' was not found. Could not install pip packages.",
            file=sys.stderr,
        )
    check_gh_cli_auth()


def reset_to_master(environments: str = None, workflows: list[str] = []) -> None:
    env_list = parse_environments(environments) if environments else read_envs_input()

    if not workflows:  # Reset affected
        workflows = get_triggered_workflows()

    if not workflows or not read_bool_input(
        f"The following workflows {workflows} will be reset for environments {environments}. Continue?",
        default=True,
    ):
        return

    _deploy_internal(workflows, env_list, True, get_repo_default_branch())


def deploy_workflow(
    workflows: list[str], environments: str = None, use_defaults: bool = True
) -> bool:
    env_list = parse_environments(environments) if environments else read_envs_input()

    if environments:
        _deploy_internal(workflows, env_list, use_defaults, get_active_branch())


def deploy_affected(
    environments: str = None, use_defaults: bool = True, maniac_mode: bool = False
) -> bool:
    env_list = parse_environments(environments) if environments else read_envs_input()

    workflows = get_triggered_workflows()

    if not maniac_mode and not read_bool_input(
        f"The following workflows {workflows} will be triggered for environments {environments}. Continue?",
        default=True,
    ):
        print(
            f"Skipping the deployment of workflows {workflows} for environments {environments}."
        )
        return

    if workflows:
        _deploy_internal(workflows, env_list, use_defaults, get_active_branch())


def _deploy_internal(
    workflows: list[str],
    env_list: list[str] = None,
    use_defaults: bool = True,
    branch: str = None,
) -> bool:
    run_tests = (
        read_bool_input("Run Tests?", default=True) if not use_defaults else None
    )
    force_rebuild = (
        read_bool_input("Force Image Rebuild?", default=True)
        if not use_defaults
        else None
    )

    for env in env_list:
        for workflow in workflows:
            deploy_gh_cli(workflow, env, run_tests, force_rebuild, branch)


def read_envs_input() -> list[str]:
    raw_envs = input("Enter environment(s) to deploy to ({AVAILABLE_ENVS_SHORT}): ")
    return parse_environments(raw_envs)


def read_bool_input(message: str, default: bool = False) -> bool:
    default_answer = "yes" if default else "no"
    possible_answers = YES_STRINGS if default else NO_STRINGS
    raw_bool = input(f"{message} (yes/no; default: {default_answer}): ")
    return raw_bool.lower() in possible_answers or not raw_bool and default


def deploy_gh_cli(
    workflow: str,
    env: str,
    run_tests: bool = None,
    force_rebuild: bool = None,
    branch: str = None,
) -> bool:
    """
    Workflows can be a workflow path or it's name
    """

    if not branch:
        branch = get_active_branch()

    param_string = f"--ref {branch} "
    param_string += f" -f ENVIRONMENT={env}"
    param_string += f" -f PERFORM_TESTS={str(run_tests).lower()}" if run_tests else ""
    param_string += (
        f" -f FORCE_IMAGE_REBUILD={str(force_rebuild).lower()}" if force_rebuild else ""
    )

    print(
        f"Starting the deployment for '{workflow}' with parameters: '{param_string}'.\n"
    )

    return (
        call_gh_cli(
            [
                "workflow",
                "run",
                workflow,
            ]
            + param_string.split(" ")
        ).returncode
        == 0
    )


def check_gh_cli_auth() -> None:
    if not is_gh_cli_installed():
        print(
            "GitHub Cli is not installed. Make sure to install it!",
            file=sys.stderr,
        )
        exit(1)

    print("GitHub Cli is installed. Checking if you are logged in...")

    if not is_gh_cli_auth(False):
        if call_gh_cli(["auth", "login"]).returncode != 0:
            print(
                "Something went wrong while authenticating to GitHub via the cli.",
                file=sys.stderr,
            )
            exit(1)
    print("GitHub is all set up. You're good to go!")


def is_gh_cli_installed() -> bool:
    return which("gh") is not None


def is_gh_cli_auth(silent: bool = True) -> bool:
    return call_gh_cli(["auth", "status"], silent).returncode == 0


def call_gh_cli(params: list[str], silent: bool = False) -> CompletedProcess:
    """
    We're using the cli as pyGithub does not seem to have a way to store a session.
    """
    return subprocess.run(
        ["gh"] + params,
        stdout=subprocess.DEVNULL if silent else None,
        stderr=subprocess.DEVNULL if silent else None,
    )


def get_active_branch() -> str:
    return repo.active_branch


def check_affected() -> list:
    wfs = get_triggered_workflows()
    if wfs:
        print(f"The workflows triggered are ({len(wfs)}):")
        for wf in wfs:
            print("- " + wf)
    else:
        print("Your changes did not trigger any workflows")


def get_triggered_workflows() -> list:
    changes = get_committed_changes()
    triggered_wfs = []
    if not changes:
        print("No changes found! Make sure you committed your changes.")
        return list()
    for filename in os.listdir(WORKFLOWS_FOLDER):
        file_path = os.path.join(WORKFLOWS_FOLDER, filename)
        if not os.path.isfile(file_path):
            continue  # No file, e.g. the composite dir
        wf_yaml = get_workflow_yaml(file_path)
        if not wf_yaml:
            print(f"Could not parse workflow at '{file_path}'.", file=sys.stderr)
            continue
        if is_workflow_triggered_by_changes(wf_yaml, changes):
            # Match -> Add wf name or filename if not named
            triggered_wfs.append(wf_yaml["name"] or filename)
    return triggered_wfs


def is_workflow_triggered_by_changes(wf_yaml: Any, changes: list[str]) -> bool:
    if not wf_yaml:
        print(f"Could not parse workflow at '{wf_yaml}'.", file=sys.stderr)
        return False  # Wf was invalid
    wf_triggers = wf_yaml.get("on", {}).get("push", {}).get("paths")
    if not wf_triggers:
        return False  # Wf has no trigger
    for change in changes:
        if any(fnmatch.fnmatch(change, glob) for glob in wf_triggers):
            return True
    return False


def get_repo_default_branch() -> str:
    return next((ref.name for ref in repo.refs if ref.name in ["master", "main"]), None)


def get_committed_changes() -> list[str]:
    changes = subprocess.check_output(
        ["git", "diff", "--name-only", f"{get_repo_default_branch()}..."]
    ).decode(sys.stdout.encoding)
    return changes.splitlines()


def get_workflow_yaml(_file_path: str) -> Any:
    with open(_file_path, "r") as file:
        data = yaml.safe_load(file)
        return data


def parse_environments(env_str: str) -> list[str]:
    envs: set = set()
    # To Lower for better comparability
    clean_env_str = env_str.lower()
    # Truncate all whitespace around commas
    clean_env_str = re.sub(r"\s*,\s*", ",", clean_env_str)
    # Replace remaining whitespace with a comma
    clean_env_str = re.sub(r"\s+", ",", clean_env_str)

    for ns in clean_env_str.split(","):
        if ns == "*":  # Single number
            envs = AVAILABLE_ENVS
            break
        elif ns == "all-devs":
            envs = envs.union(set(AVAILABLE_ENVS).difference(set("staging")))
        elif ns.isdigit() and f"{int(ns):02d}" in AVAILABLE_ENVS:  # Number
            envs.add(ns.zfill(2))  # Pad front with zeros
        elif ns in AVAILABLE_ENVS:  # Special names
            envs.add(ns)
        elif "-" in ns:  # Range
            envs = envs.union(parse_as_envs_set(ns))
        else:
            print(f"Invalid value for environments found: '{ns}'", file=sys.stderr)

    return sorted(list(envs))  # Filter duplicates and order


def parse_as_envs_set(
    _range_str: str, _min: int = 1, _max: int = CONSECUTIVE_ENVS
) -> set:
    match = re.match(r"(\d+)-(\d+)", _range_str)
    if match:
        start = int(match.group(1))
        end = int(match.group(2))

        if not 1 <= start <= end <= CONSECUTIVE_ENVS:
            print(
                f"Start/End of the range was not valid: '{_range_str}'. Must be in range [{_min}, {_max}]",
                file=sys.stderr,
            )
        return set([f"{i:02d}" for i in range(start, end + 1)])
    print(f"Invalid range format: '{_range_str}'", file=sys.stderr)
    return set()


def bool_constructor(loader: yaml.Loader, node: yaml.Node) -> Any:
    # We need to overwrite the parsing of booleans in yaml, as GitHub decided to use "on" (like on/off) as key
    value = loader.construct_scalar(node)
    if value == "on":
        return value
    else:
        return True if value.lower() == "true" else False


SafeConstructor.add_constructor("tag:yaml.org,2002:bool", bool_constructor)


def main(_argv=sys.argv[1:]) -> None:
    # Main program
    parser = argparse.ArgumentParser(
        prog="bf-deploy",
        description='The "Bergfreunde"-tool for quick GitHub-deployments',
    )
    subparsers = parser.add_subparsers()

    # Parser init
    parser_init = subparsers.add_parser(
        "init",
        aliases=["i"],
        allow_abbrev=True,
        help="Initializes the bf deploy script by installing all required packages and authorizing to github.",
    )
    parser_init.set_defaults(cmd=init_bfd)

    # Parser auth
    parser_auth = subparsers.add_parser(
        "auth",
        allow_abbrev=True,
        help="Check your installation and authenticate yourself to GitHub.",
    )
    parser_auth.set_defaults(cmd=check_gh_cli_auth)

    # Parser affected
    parser_affected = subparsers.add_parser(
        "affected",
        aliases=["a"],
        allow_abbrev=True,
        help="Find all workflows that will be triggered by your committed changes.",
    )
    parser_affected.set_defaults(cmd=check_affected)

    # Parser deploy
    parser_deploy = subparsers.add_parser(
        "deploy",
        aliases=["d"],
        allow_abbrev=True,
        help="Trigger a deployment to the given environment(s) of your branch for the workflow(s).",
    )
    parser_deploy.add_argument("workflows", nargs="+", help="Workflows to be deployed.")
    parser_deploy.add_argument(
        "--environments",
        "-e",
        help=f"Environment to be deployed to: ({AVAILABLE_ENVS_SHORT}). Can be a single value or a list, separated by commas without spaces.",
    )
    parser_deploy.add_argument(
        "--use-defaults",
        "-d",
        action="store_true",
        help="If optional values should not be requested and the default values are used.",
    )
    parser_deploy.set_defaults(cmd=deploy_workflow)

    # Deploy affected
    parser_deploy_affected = subparsers.add_parser(
        "deploy-affected",
        aliases=["da"],
        help="Deploys all workflows that are affected by your changes to the environment(s).",
    )
    parser_deploy_affected.add_argument(
        "--environments",
        "-e",
        help=f"Environment to be deployed to: ({AVAILABLE_ENVS_SHORT}). Can be a single value or a list, separated by commas without spaces.",
    )
    parser_deploy_affected.add_argument(
        "--use-defaults",
        "-d",
        action="store_true",
        help="If optional values should not be requested and the default values are used.",
    )
    parser_deploy_affected.add_argument(
        "--maniac-mode",
        "-m",
        action="store_true",
        help="Do require an additional confirmation after parsing the environments.",
    )
    parser_deploy_affected.set_defaults(cmd=deploy_affected)

    # Reset
    parser_reset = subparsers.add_parser(
        "reset",
        aliases=["r"],
        allow_abbrev=True,
        help="Deploys the given workflows using the current master for the given environments.",
    )
    parser_reset.add_argument("workflows", nargs="+", help="Workflows to be deployed.")
    parser_reset.add_argument(
        "--environments",
        "-e",
        help=f"Environment to be deployed to: ({AVAILABLE_ENVS_SHORT}). Can be a single value or a list, separated by commas without spaces.",
    )
    parser_reset.set_defaults(cmd=reset_to_master)

    # Reset affected
    parser_reset = subparsers.add_parser(
        "reset-affected",
        aliases=["ra"],
        allow_abbrev=True,
        help="Deploys the given workflows using the current master for the given environments.",
    )
    parser_reset.add_argument(
        "--environments",
        "-e",
        help=f"Environment to be deployed to: ({AVAILABLE_ENVS_SHORT}). Can be a single value or a list, separated by commas without spaces.",
    )
    parser_reset.set_defaults(cmd=reset_to_master)

    # Retrieve dict of namespaces
    args = parser.parse_args(_argv)
    args_dict = vars(args)

    # Retrieve cmd and call it with the remaining vars
    if "cmd" in args_dict:
        cmd = args_dict.pop("cmd")
        cmd(**args_dict)
    else:
        parser.print_help(sys.stderr)


if __name__ == "__main__":
    main()
