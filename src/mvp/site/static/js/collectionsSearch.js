import Fuse from './vendor/fuse.js';

function collectionsSearch(inputId, resultsId, spinnerId) {
    const input = document.getElementById(inputId);
    const results = document.getElementById(resultsId);
    const list = results ? results.querySelector('.collapse-content ul') : null;
    const spinner = spinnerId ? document.getElementById(spinnerId) : null;
    if (!input || !results || !list) return;

    var fuse = null;

    function render(matches) {
        list.innerHTML = '';
        if (!matches.length) {
            results.classList.remove('collapse-open');
            return;
        }
        matches.slice(0, 50).forEach(function (entry) {
            const li = document.createElement('li');
            const a = document.createElement('a');
            a.href = entry.url;
            a.className = 'link block px-3 py-1.5 text-sm hover:bg-neutral-100';

            var label = entry.title + ' (' + entry.language + ')';
            if (entry.author) label += ' — ' + entry.author;
            if (entry.editors) label += " — " + entry.editors;
            a.textContent = label;
            li.appendChild(a);
            list.appendChild(li);
        });
        results.classList.add('collapse-open');
    }

    function search(query) {
        query = query.trim();
        if (!query) {
            render([]);
            return;
        }
        const matches = fuse.search(query, { limit: 100 }).map(function (result) {
            return result.item;
        });
        render(matches);
    }

    input.addEventListener('input', function () {
        if (fuse) {
            search(input.value);
            return;
        }
        if (spinner) spinner.classList.remove('hidden');
        fetch('/collections/search-index.json')
            .then(function (r) { return r.json(); })
            .then(function (data) {
                fuse = new Fuse(data, {
                    keys: ['title', 'author', 'editors'],
                    threshold: 0.3,
                });
                search(input.value);
            })
            .finally(function () {
                if (spinner) spinner.classList.add('hidden');
            });
    });

    document.addEventListener('click', function (event) {
        if (event.target !== input) {
            results.classList.remove('collapse-open');
        }
    });
}

window.collectionsSearch = collectionsSearch;
