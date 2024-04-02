#!/bin/bash

declare -r DEFAULT_WF_TRIGGERS_FILE=.triggers.json

# Usage: change_files=($(get_changed_files))
function get_changed_files() {
    main_branch=$(git symbolic-ref refs/remotes/origin/HEAD | sed 's@^refs/remotes/origin/@@')
    git diff --name-only $main_branch...
}

# Params: $1 = message, $2 = default_value, $3 = use_defaults
# Usage: my_bool=$(get_changed_files)
function get_bool_input() {
    msg=$1
    default_value=$2
    use_defaults=$3
    if $use_defaults ; then
        echo $default_value
    else
        read -p "$msg (true/false, default: $default_value) " confirm
        confirm_lowercase=$(echo "$confirm" | tr '[:upper:]' '[:lower:]')
        if $default_value ; then
            [[ $confirm_lowercase =~ ^[nf0]|no|false$ ]] && echo false || echo true 
        else
            [[ $confirm_lowercase =~ ^[yt1]|yes|true$ ]] && echo true || echo false 
        fi
    fi
}


function render_workflow_triggers() {
    to_file="$1"
    shift

    # Write the output to the target file if it was provided
    if [ -n "$to_file" ] ; then
        [ -d foo ] || mkdir -p $(dirname $to_file)
        render_workflow_triggers > "$to_file"
        return
    fi

    # Write output to stdout
    for workflow in .github/workflows/*.yml ; do
        [ -n "$workflow" ] || continue
        triggers=$(yq eval '.on.pull_request.paths | ... comments=""' -o=yaml "$workflow")
        if [ "$triggers" != "null" ] ; then
            workflow_name=$(yq eval '.name' -o=yaml "$workflow")
            [ -n "$workflow_name" ] && echo "$workflow_name:" || echo "$workflow:"
            echo "$triggers"
        fi
    done
}


function check_yq_installed() {
    if ! command -v yq  > /dev/null 2>&1 ; then
        echo -n "Warning: yq is not installed. Installing it from source..." >&2
        sudo wget https://github.com/mikefarah/yq/releases/latest/download/yq_linux_amd64 -q -O /usr/bin/yq && \
        sudo chmod +x /usr/bin/yq
        echo " Done."
    fi

    # The version check needs to be updated
    if ! yq --version | grep v4 > /dev/null 2>&1 ; then
        if ! gh auth login ; then
            echo "Error: Your yq version is wrong, please update it to a version greater than 4.0!" >&2
            exit 1
        fi
    fi

    echo "yq is installed an has the correct minimal version."
}


function check_gh_login() {
    silent=$([[ "$1" =~ ^-s|--silent$ ]] && echo true || echo false)
    if ! command -v gh  > /dev/null 2>&1 ; then
        echo "Error: GitHub Cli is not installed. Please install it!" >&2
        exit 1
    fi

    if ! gh auth status > /dev/null 2>&1 ; then
        if ! gh auth login ; then
            echo "Error: GitHub authentification failed!" >&2
            exit 1
        fi
    fi
    
    [ "$silent" == "false" ] && echo "Github is all set. You are good to go!"
}


function has_workflow_trigger() {
    if [ -z $1 ] || [ -z $2 ] ; then
        return 1 #false
    fi

    workflow=$1
    shift
    changes=($@)

    wildcard_patterns=($(yq eval-all '.on.pull_request.paths[]' -o=yaml "$workflow"))

    if [ ${#wildcard_patterns[@]} -eq 0 ] ; then
        return 1 # false
    fi

    for file in "${changes[@]}" ; do
        for pattern in "${wildcard_patterns[@]}" ; do
            if [[ $file =~ $pattern ]]; then
                return 0 # true
            fi
        done
    done
    return 1 # false
}


function has_cached_workflow_trigger() {
    changed_files=($(get_changed_files))
    if [ -z $changed_files ] ; then
        echo "No changes to check against provided" >&2
        return 1 #false
    fi

    workflows=($(yq eval 'keys | .[]' "$DEFAULT_WF_TRIGGERS_FILE"))

    for workflow in "${workflows[@]}" ; do
        wildcard_patterns=($(yq eval ".$workflow[]" "$DEFAULT_WF_TRIGGERS_FILE"))

        if [ ${#wildcard_patterns[@]} -eq 0 ] ; then
            return 1 # false
        fi

        for file in "${changed_files[@]}" ; do
            for pattern in "${wildcard_patterns[@]}" ; do
                if [[ $file =~ $pattern ]]; then
                    return 0 # true
                fi
            done
        done
    done
    return 1 # false
}

function has_cached_workflow_trigger2() {
    changed_files=($(get_changed_files))
    if [ -z $changed_files ] ; then
        echo "No changes to check against provided" >&2
        return 1 #false
    fi

    workflows=($(jq '. | to_entries | .[].key' "triggers.json"))

    for workflow in "${workflows[@]}" ; do
        wildcard_patterns=($(cat "triggers.json" | jq -r ".$workflow[]"))

        if [ ${#wildcard_patterns[@]} -eq 0 ] ; then
            #echo "false"
            return 1 # false
        fi

        for file in "${changed_files[@]}" ; do
            for pattern in "${wildcard_patterns[@]}" ; do
                if [[ $file =~ $pattern ]]; then
                    return 0 # true
                fi
            done
        done
    done
    return 1 # false
}

function check_affected() {
    print_list="$1"
    shift
    workflows=".github/workflows/*.yml" # Only iterate over top-level of the directory 
    changed_files=($(get_changed_files))

    if [ -z $changed_files ] ; then
        echo "No changes detected in you current branch. Make sure that you have commited your changes!"
        exit 1
    fi

    if [ "$print_list" != "list" ] ; then
        echo $'Checking which workflows are affected by your changes...\n'
    fi

    for workflow in $workflows ; do
        [ -n "$workflow" ] || continue

        if has_workflow_trigger $workflow ${changed_files[@]} ; then
            if [ "$print_list" == "list" ] ; then
                echo -n "$workflow,"
            else
                echo "- $workflow"
            fi
        fi
    done

    if [ "$print_list" != "list" ] ; then
        echo $'\nDone checking affected workflows.'
    fi
}

# Params: $1 = use_defaults
function read_workflow_inputs() {
    # Currently we are planning on only using the inputs:
    # ENVIRONMENT, (opt) RUN_TESTS and (opt) FORCE_IMAGE_REBUILD

    # Check if defaults should be used, false if omitted
    use_defaults="$( [ -n "$1" ] && echo $1 || echo false)"

    read -p "Enter ENVIRONMENT to deploy to (01-16|oms|staging): " ENVIRONMENT && \
        [[ ! $ENVIRONMENT =~ ^(0?[1-9]|(1[0-6])|oms|staging)$ ]] && \
        echo "ENVIRONMENT not in the range of '01-16' or 'oms'" >&2 && \
        exit 1
    
    # Add leading zeros if ommitted
    if [[ $ENVIRONMENT =~ ^[1-9]$ ]]; then
        ENVIRONMENT="0$ENVIRONMENT"
    fi

    PERFORM_TESTS=$( get_bool_input "Run Tests?" true $use_defaults)
    FORCE_IMAGE_REBUILD=$( get_bool_input "Force Image Rebuild?" true $use_defaults)
}


function deploy_workflow() {
    # Check for default value flag
    use_defaults=$([[ "$1" =~ ^-d|--defaults?$ ]] && echo true || echo false)
    $use_defaults && shift

    # Use the remaining inputs as workflows
    read -r -a workflows <<< "$@"
    branch=$(git branch --show-current)

    if [ ${#workflows[@]} -eq 0 ] ; then
        echo "Can't deploy, no workflows were provided!" >&2
        exit 1
    fi

    read_workflow_inputs $use_defaults

    echo "Starting deployment to environment: '$ENVIRONMENT' with PERFORM_TESTS=$PERFORM_TESTS and FORCE_IMAGE_REBUILD=$FORCE_IMAGE_REBUILD"

    for workflow in "${workflows[@]}" ; do
        echo $'-------------------------------------------------------'
        gh workflow run $workflow --ref $branch \
            -f ENVIRONMENT=$ENVIRONMENT \
            -f PERFORM_TESTS=$PERFORM_TESTS \
            -f FORCE_IMAGE_REBUILD=$FORCE_IMAGE_REBUILD
    done
}

function deploy_affected_workflows() {
    wfs=$(check_affected list)
    # Probably no changed files (commited)
    [ $? -ne 0 ] && echo $wfs >&2 && exit 1
    # No affected workflows
    [ "$wfs" == "" ] && echo "Did not find any workflows triggered by your changes!" >&2 && exit 1
    deploy_workflow $wfs
}


function do_task() {
    case "$1" in
        auth)
            shift
            check_gh_login
            check_yq_installed
            ;;

        affected)
            shift
            check_affected
            ;;
        
        deploy)
            shift
            check_gh_login --silent
            deploy_workflow $@
            ;;
        
        deploy-affected)
            shift
            check_gh_login --silent
            deploy_affected_workflows
            ;;
        
        testing)
            shift
            has_cached_workflow_trigger2
            ;;
        
        free)
            shift
            echo "TODO: Maybe mark workflow as freed"
            ;;

        render)
            shift
            render_workflow_triggers $1
            ;;

        *|-h|--help)
            echo "help"
            ;;

    esac
}

do_task $@
