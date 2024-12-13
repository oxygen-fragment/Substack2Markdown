import argparse
import json
import os
from abc import ABC, abstractmethod
from typing import List, Optional, Tuple
from time import sleep

from bs4 import BeautifulSoup
import html2text
import markdown
import requests
from tqdm import tqdm
from xml.etree import ElementTree as ET

from selenium import webdriver
from selenium.webdriver.common.by import By
from webdriver_manager.microsoft import EdgeChromiumDriverManager
from selenium.webdriver.edge.options import Options as EdgeOptions
from selenium.webdriver.chrome.service import Service
from urllib.parse import urlparse
from config import EMAIL, PASSWORD

USE_PREMIUM: bool = False  # Set to True if you want to login to Substack and convert paid for posts
BASE_SUBSTACK_URL: str = "https://www.thefitzwilliam.com/"  # Substack you want to convert to markdown
BASE_MD_DIR: str = "substack_md_files"  # Name of the directory we'll save the .md essay files
BASE_HTML_DIR: str = "substack_html_pages"  # Name of the directory we'll save the .html essay files
HTML_TEMPLATE: str = "author_template.html"  # HTML template to use for the author page
JSON_DATA_DIR: str = "data"
NUM_POSTS_TO_SCRAPE: int = 3  # Set to 0 if you want all posts


def extract_main_part(url: str) -> str:
    parts = urlparse(url).netloc.split('.')  
    return parts[1] if parts[0] == 'www' else parts[0]


def generate_html_file(author_name: str) -> None:
    if not os.path.exists(BASE_HTML_DIR):
        os.makedirs(BASE_HTML_DIR)

    json_path = os.path.join(JSON_DATA_DIR, f'{author_name}.json')
    with open(json_path, 'r', encoding='utf-8') as file:
        essays_data = json.load(file)

    embedded_json_data = json.dumps(essays_data, ensure_ascii=False, indent=4)

    with open(HTML_TEMPLATE, 'r', encoding='utf-8') as file:
        html_template = file.read()

    html_with_data = html_template.replace('<!-- AUTHOR_NAME -->', author_name).replace(
        '<script type="application/json" id="essaysData"></script>',
        f'<script type="application/json" id="essaysData">{embedded_json_data}</script>'
    )
    html_with_author = html_with_data.replace('author_name', author_name)

    html_output_path = os.path.join(BASE_HTML_DIR, f'{author_name}.html')
    with open(html_output_path, 'w', encoding='utf-8') as file:
        file.write(html_with_author)


class BaseSubstackScraper(ABC):
    def __init__(self, base_substack_url: str, md_save_dir: str, html_save_dir: str):
        if not base_substack_url.endswith("/"):
            base_substack_url += "/"
        self.base_substack_url: str = base_substack_url

        self.writer_name: str = extract_main_part(base_substack_url)
        md_save_dir: str = f"{md_save_dir}/{self.writer_name}"

        self.md_save_dir: str = md_save_dir
        self.html_save_dir: str = f"{html_save_dir}/{self.writer_name}"

        if not os.path.exists(md_save_dir):
            os.makedirs(md_save_dir)
            print(f"Created md directory {md_save_dir}")
        if not os.path.exists(self.html_save_dir):
            os.makedirs(self.html_save_dir)
            print(f"Created html directory {self.html_save_dir}")

        self.keywords: List[str] = ["about", "archive", "podcast"]
        self.post_urls: List[str] = self.get_all_post_urls()

    def get_all_post_urls(self) -> List[str]:
        urls = self.fetch_urls_from_sitemap()
        if not urls:
            urls = self.fetch_urls_from_feed()
        return self.filter_urls(urls, self.keywords)

    def fetch_urls_from_sitemap(self) -> List[str]:
        sitemap_url = f"{self.base_substack_url}sitemap.xml"
        response = requests.get(sitemap_url)

        if not response.ok:
            print(f'Error fetching sitemap at {sitemap_url}: {response.status_code}')
            return []

        root = ET.fromstring(response.content)
        urls = [element.text for element in root.iter('{http://www.sitemaps.org/schemas/sitemap/0.9}loc')]
        return urls

    def fetch_urls_from_feed(self) -> List[str]:
        print('Falling back to feed.xml. This will only contain up to the 22 most recent posts.')
        feed_url = f"{self.base_substack_url}feed.xml"
        response = requests.get(feed_url)

        if not response.ok:
            print(f'Error fetching feed at {feed_url}: {response.status_code}')
            return []

        root = ET.fromstring(response.content)
        urls = []
        for item in root.findall('.//item'):
            link = item.find('link')
            if link is not None and link.text:
                urls.append(link.text)

        return urls

    @staticmethod
    def filter_urls(urls: List[str], keywords: List[str]) -> List[str]:
        return [url for url in urls if all(keyword not in url for keyword in keywords)]

    @staticmethod
    def html_to_md(html_content: str) -> str:
        if not isinstance(html_content, str):
            raise ValueError("html_content must be a string")
        h = html2text.HTML2Text()
        h.ignore_links = False
        h.body_width = 0
        return h.handle(html_content)

    @staticmethod
    def save_to_file(filepath: str, content: str) -> None:
        if not isinstance(filepath, str):
            raise ValueError("filepath must be a string")

        if not isinstance(content, str):
            raise ValueError("content must be a string")

        if os.path.exists(filepath):
            print(f"File already exists: {filepath}")
            return

        with open(filepath, 'w', encoding='utf-8') as file:
            file.write(content)

    @staticmethod
    def md_to_html(md_content: str) -> str:
        return markdown.markdown(md_content, extensions=['extra'])

    def save_to_html_file(self, filepath: str, content: str) -> None:
        if not isinstance(filepath, str):
            raise ValueError("filepath must be a string")

        if not isinstance(content, str):
            raise ValueError("content must be a string")

        html_dir = os.path.dirname(filepath)
        css_path = os.path.relpath("./assets/css/essay-styles.css", html_dir)
        css_path = css_path.replace("\\", "/")

        html_content = f"""
            <!DOCTYPE html>
            <html lang="en">
            <head>
                <meta charset="UTF-8">
                <meta name="viewport" content="width=device-width, initial-scale=1.0">
                <title>Markdown Content</title>
                <link rel="stylesheet" href="{css_path}">
            </head>
            <body>
                <main class="markdown-content">
                {content}
                </main>
            </body>
            </html>
        """

        with open(filepath, 'w', encoding='utf-8') as file:
            file.write(html_content)

    @staticmethod
    def get_filename_from_url(url: str, filetype: str = ".md") -> str:
        if not isinstance(url, str):
            raise ValueError("url must be a string")

        if not isinstance(filetype, str):
            raise ValueError("filetype must be a string")

        if not filetype.startswith("."):
            filetype = f".{filetype}"

        return url.split("/")[-1] + filetype

    @staticmethod
    def combine_metadata_and_content(title: str, subtitle: str, date: str, like_count: str, content) -> str:
        if not isinstance(title, str):
            raise ValueError("title must be a string")

        if not isinstance(content, str):
            raise ValueError("content must be a string")

        metadata = f"# {title}\n\n"
        if subtitle:
            metadata += f"## {subtitle}\n\n"
        metadata += f"**{date}**\n\n"
        metadata += f"**Likes:** {like_count}\n\n"

        return metadata + content

    def extract_post_data(self, soup: BeautifulSoup) -> Optional[Tuple[str, str, str, str, str]]:
        title_element = soup.select_one("h1.post-title, h2")
        if title_element is None or not title_element.text.strip():
            print("No title found, skipping this post.")
            return None

        title = title_element.text.strip()

        subtitle_element = soup.select_one("h3.subtitle")
        subtitle = subtitle_element.text.strip() if subtitle_element and subtitle_element.text else ""

        date_element = soup.find(
            "div",
            class_="pencraft pc-reset _color-pub-secondary-text_3axfk_207 _line-height-20_3axfk_95 _font-meta_3axfk_131 _size-11_3axfk_35 _weight-medium_3axfk_162 _transform-uppercase_3axfk_242 _reset_3axfk_1 _meta_3axfk_442"
        )
        date = date_element.text.strip() if date_element and date_element.text else "Date not found"

        like_count_element = soup.select_one("a.post-ufi-button .label")
        like_count = (
            like_count_element.text.strip()
            if like_count_element and like_count_element.text and like_count_element.text.strip().isdigit()
            else "0"
        )

        content_container = soup.select_one("div.available-content")
        if content_container is None:
            print("No content container found, skipping this post.")
            return None

        content = str(content_container)
        md = self.html_to_md(content)
        md_content = self.combine_metadata_and_content(title, subtitle, date, like_count, md)
        return title, subtitle, like_count, date, md_content

    @abstractmethod
    def get_url_soup(self, url: str) -> Optional[BeautifulSoup]:
        raise NotImplementedError

    def save_essays_data_to_json(self, essays_data: list) -> None:
        data_dir = os.path.join(JSON_DATA_DIR)
        if not os.path.exists(data_dir):
            os.makedirs(data_dir)

        json_path = os.path.join(data_dir, f'{self.writer_name}.json')
        if os.path.exists(json_path):
            with open(json_path, 'r', encoding='utf-8') as file:
                existing_data = json.load(file)
            essays_data = existing_data + [data for data in essays_data if data not in existing_data]
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(essays_data, f, ensure_ascii=False, indent=4)

    def scrape_posts(self, num_posts_to_scrape: int = 0) -> None:
        essays_data = []
        count = 0
        total = num_posts_to_scrape if num_posts_to_scrape != 0 else len(self.post_urls)
        for url in tqdm(self.post_urls, total=total):
            try:
                md_filename = self.get_filename_from_url(url, filetype=".md")
                html_filename = self.get_filename_from_url(url, filetype=".html")
                md_filepath = os.path.join(self.md_save_dir, md_filename)
                html_filepath = os.path.join(self.html_save_dir, html_filename)

                if not os.path.exists(md_filepath):
                    soup = self.get_url_soup(url)
                    if soup is None:
                        print(f"Unable to fetch soup for {url}, skipping.")
                        # Adjust total if we can't process this one
                        if num_posts_to_scrape == 0:
                            total -= 1
                        continue

                    post_data = self.extract_post_data(soup)
                    if post_data is None:
                        print(f"Skipping post due to missing data: {url}")
                        if num_posts_to_scrape == 0:
                            total -= 1
                        continue

                    title, subtitle, like_count, date, md = post_data
                    self.save_to_file(md_filepath, md)

                    html_content = self.md_to_html(md)
                    self.save_to_html_file(html_filepath, html_content)

                    essays_data.append({
                        "title": title,
                        "subtitle": subtitle,
                        "like_count": like_count,
                        "date": date,
                        "file_link": md_filepath,
                        "html_link": html_filepath
                    })
                else:
                    print(f"File already exists: {md_filepath}")
            except Exception as e:
                print(f"Error scraping post: {e}")
            count += 1
            if num_posts_to_scrape != 0 and count == num_posts_to_scrape:
                break
        self.save_essays_data_to_json(essays_data=essays_data)
        generate_html_file(author_name=self.writer_name)


class SubstackScraper(BaseSubstackScraper):
    def __init__(self, base_substack_url: str, md_save_dir: str, html_save_dir: str):
        super().__init__(base_substack_url, md_save_dir, html_save_dir)

    def get_url_soup(self, url: str) -> Optional[BeautifulSoup]:
        try:
            page = requests.get(url, headers=None)
            soup = BeautifulSoup(page.content, "html.parser")
            if soup.find("h2", class_="paywall-title"):
                print(f"Skipping premium article: {url}")
                return None
            return soup
        except Exception as e:
            raise ValueError(f"Error fetching page: {e}") from e


class PremiumSubstackScraper(BaseSubstackScraper):
    def __init__(
            self,
            base_substack_url: str,
            md_save_dir: str,
            html_save_dir: str,
            headless: bool = False,
            edge_path: str = '',
            edge_driver_path: str = '',
            user_agent: str = ''
    ) -> None:
        super().__init__(base_substack_url, md_save_dir, html_save_dir)

        options = EdgeOptions()
        if headless:
            options.add_argument("--headless")
        if edge_path:
            options.binary_location = edge_path
        if user_agent:
            options.add_argument(f'user-agent={user_agent}')  

        if edge_driver_path:
            service = Service(executable_path=edge_driver_path)
        else:
            service = Service(EdgeChromiumDriverManager().install())

        self.driver = webdriver.Edge(service=service, options=options)
        self.login()

    def login(self) -> None:
        self.driver.get("https://substack.com/sign-in")
        sleep(3)

        signin_with_password = self.driver.find_element(
            By.XPATH, "//a[@class='login-option substack-login__login-option']"
        )
        signin_with_password.click()
        sleep(3)

        email = self.driver.find_element(By.NAME, "email")
        password = self.driver.find_element(By.NAME, "password")
        email.send_keys(EMAIL)
        password.send_keys(PASSWORD)

        submit = self.driver.find_element(By.XPATH, "//*[@id=\"substack-login\"]/div[2]/div[2]/form/button")
        submit.click()
        sleep(30)

        if self.is_login_failed():
            raise Exception(
                "Warning: Login unsuccessful. Check credentials or captcha."
            )

    def is_login_failed(self) -> bool:
        error_container = self.driver.find_elements(By.ID, 'error-container')
        return len(error_container) > 0 and error_container[0].is_displayed()

    def get_url_soup(self, url: str) -> Optional[BeautifulSoup]:
        try:
            self.driver.get(url)
            return BeautifulSoup(self.driver.page_source, "html.parser")
        except Exception as e:
            raise ValueError(f"Error fetching page: {e}") from e


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scrape a Substack site.")
    parser.add_argument("-u", "--url", type=str, help="The base URL of the Substack site to scrape.")
    parser.add_argument("-d", "--directory", type=str, help="The directory to save scraped posts.")
    parser.add_argument(
        "-n",
        "--number",
        type=int,
        default=0,
        help="The number of posts to scrape. If 0 or not provided, all posts will be scraped.",
    )
    parser.add_argument("-p", "--premium", action="store_true", help="Use the Premium Substack Scraper.")
    parser.add_argument("--headless", action="store_true", help="Run browser in headless mode.")
    parser.add_argument("--edge-path", type=str, default="", help='Path to the Edge browser executable.')
    parser.add_argument("--edge-driver-path", type=str, default="", help='Path to the Edge WebDriver executable.')
    parser.add_argument("--user-agent", type=str, default="", help="Specify a custom user agent.")
    parser.add_argument("--html-directory", type=str, help="The directory to save scraped posts as HTML files.")

    return parser.parse_args()


def main():
    args = parse_args()

    if args.directory is None:
        args.directory = BASE_MD_DIR

    if args.html_directory is None:
        args.html_directory = BASE_HTML_DIR

    if args.url:
        if args.premium:
            scraper = PremiumSubstackScraper(
                args.url,
                headless=args.headless,
                md_save_dir=args.directory,
                html_save_dir=args.html_directory,
                edge_path=args.edge_path,
                edge_driver_path=args.edge_driver_path,
                user_agent=args.user_agent
            )
        else:
            scraper = SubstackScraper(
                args.url,
                md_save_dir=args.directory,
                html_save_dir=args.html_directory
            )
        scraper.scrape_posts(args.number)

    else:
        if USE_PREMIUM:
            scraper = PremiumSubstackScraper(
                base_substack_url=BASE_SUBSTACK_URL,
                md_save_dir=args.directory,
                html_save_dir=args.html_directory,
                edge_path=args.edge_path,
                edge_driver_path=args.edge_driver_path
            )
        else:
            scraper = SubstackScraper(
                base_substack_url=BASE_SUBSTACK_URL,
                md_save_dir=args.directory,
                html_save_dir=args.html_directory
            )
        scraper.scrape_posts(num_posts_to_scrape=NUM_POSTS_TO_SCRAPE)


if __name__ == "__main__":
    main()
